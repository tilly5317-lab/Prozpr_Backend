# Unified Chat Modules — design

**Date:** 2026-04-28
**Status:** Design
**Owner:** Amoul
**Supersedes:** the routing-rule logic and `asset_allocation_followup` split introduced in 2026-04-27-asset-allocation-followup-design.md (the AgentRun storage + persistence + classifier signals from that spec stay; only the routing/dispatch layer is being re-architected).

## Summary

The current chat architecture splits responsibility for one user intent ("talk to me about my allocation") across three places: the brain routing rule, the engine wrapper (`asset_allocation_service`), and the followup responder (`asset_allocation_followup`). Each new edge case requires patching the classifier prompt, the brain, or the followup prompt — and a wrong call at the brain level bypasses the followup entirely. We're prompt-engineering routing decisions in the wrong layer.

This redesign collapses the chat-side logic for each intent family into a **single chat module per intent**. The brain becomes a thin dispatcher; the per-intent chat module is the sole owner of every decision (run engine / narrate / counterfactual / clarify / recompute / redirect / educate). The classifier shrinks back to its real job: "which intent family is this?" — much easier than "should we re-run the engine?"

We rip-and-replace because the current behavior is not deployed in prod.

## Goals

- One chat module per intent family. Single owner of all chat-side decisions for that intent.
- Brain becomes intent → dispatcher; no routing intelligence beyond intent.
- Classifier loses load-bearing flags (`wants_fresh_recomputation`) — they're no longer needed.
- New behaviors (modes / edge cases) land in *one file* per intent. No prompt patches across multiple layers.
- Engine wrapper (`compute_allocation_result`) stays a pure function — usable by chat AND by the standalone HTTP endpoint.
- Pattern generalizes cleanly to rebalancing and other future modules.

## Non-goals

- Multi-turn stateful clarification (bot asks "what risk?" → user replies "7" → bot remembers the question). Each turn is independent for now.
- Cross-session memory. Each session starts fresh.
- Migrating market_commentary / portfolio_query / general_chat to the unified pattern. Those don't currently have AgentRun persistence; deferred until they're touched anyway.
- Tool-using agentic responder. The unified module is a structured-output dispatcher, not a tool-calling loop.

## Locked design decisions

| Decision | Choice | Why |
|---|---|---|
| Routing layer | Brain dispatches to per-intent chat module on intent only | Best context (snapshot + question) lives in the chat module; routing belongs there |
| Module granularity | One chat module per intent family | Single source of decision intelligence per domain; new modes are local edits |
| Engine wrapper boundary | `compute_allocation_result` stays a pure function | Standalone HTTP endpoint and chat both consume it; no chat concepts leak into the engine |
| First-turn handling | Inside the unified chat module | Brain doesn't need to distinguish first-turn from follow-up |
| Classifier flags | Drop `wants_fresh_recomputation` | No longer load-bearing; the chat module decides recompute |
| Migration approach | Rip-and-replace | Not deployed in prod; clean end-state |

## Architecture overview

```
ChatBrain.run_turn(turn)
│
├── build_turn_context(turn)               # unchanged
│
├── classify_user_message(question, history, active_intent)
│       returns: ClassificationResult(intent, confidence, is_follow_up, reasoning)
│       (wants_fresh_recomputation removed)
│
└── dispatch by intent family:
    │
    if intent in (portfolio_optimisation, goal_planning):
        → asset_allocation_chat.handle(turn_context)
            ├── if no last AgentRun for goal_based_allocation → run engine, persist, return brief
            └── else → detect_action(snapshot + question + history)
                    ├── narrate                  (no engine, narrate snapshot)
                    ├── counterfactual_explore   (engine with overrides, NO persist)
                    ├── clarify                  (compose question, no engine)
                    ├── recompute_full           (engine fresh, persist as new plan)
                    ├── recompute_with_overrides (engine with overrides, persist as new plan)
                    └── redirect                 (templated Profile redirect)

    elif intent == general_market_query:
        → unchanged today (market_commentary + general_chat path)
    elif intent == portfolio_query:
        → unchanged today (portfolio_query_service path)
    else:
        → general_chat (unchanged)
```

The **brain stays small**. Each branch is one dispatch line. The per-intent module owns everything domain-specific.

## Components

### 1. New: `app/services/ai_bridge/asset_allocation_chat.py`

The unified chat module for `portfolio_optimisation` / `goal_planning` intents. Replaces and absorbs both the chat-side parts of `asset_allocation_service.py` (chat-brief formatting, flow trace) AND the entirety of `asset_allocation_followup.py` + `asset_allocation_followup_counterfactual.py`.

```python
@register("portfolio_optimisation")
@register("goal_planning")
async def handle(ctx: TurnContext) -> ChatHandlerResult:
    """Sole entry point for chat turns in this intent family."""
    last_alloc = ctx.last_agent_runs.get("goal_based_allocation")

    if last_alloc is None:
        return await _first_turn_run_engine(ctx)

    action = await _detect_action(last_alloc, ctx)
    return await _dispatch_action(action, last_alloc, ctx)
```

Where:
- `_first_turn_run_engine(ctx)` calls `compute_allocation_result`, formats the chat brief, persists AgentRun, returns `(text, snapshot_id, recommendation_id)`.
- `_detect_action` is one Haiku call → `ChatAction` with mode + (mode-specific fields).
- `_dispatch_action` branches on `action.mode` and calls the per-mode handler.

`ChatHandlerResult` is a small dataclass:
```python
@dataclass(frozen=True)
class ChatHandlerResult:
    text: str
    snapshot_id: uuid.UUID | None = None
    rebalancing_recommendation_id: uuid.UUID | None = None
```

`ChatAction` is the structured output of `_detect_action`:
```python
class ChatAction(BaseModel):
    mode: Literal[
        "narrate",
        "educate",
        "counterfactual_explore",
        "clarify",
        "recompute_full",
        "recompute_with_overrides",
        "redirect",
    ]
    overrides: Optional[dict[str, Any]] = None      # for counterfactual_explore + recompute_with_overrides
    clarification_question: Optional[str] = None    # for clarify
    redirect_reason: Optional[str] = None           # for redirect
```

**Allowed override keys for counterfactual / recompute_with_overrides:**

| Key | Type | Use case |
|---|---|---|
| `effective_risk_score` | float (1–10) | "What if my risk were 7?" |
| `total_corpus` | float (≥0) | "What if I had ₹1 crore to invest?" |
| `annual_income` | float (≥0) | "What if my income doubled?" |
| `monthly_household_expense` | float (≥0) | "What if my expenses dropped?" |
| `emergency_fund_needed` | bool | "What if I skipped emergency fund?" |
| `tax_regime` | "old" \| "new" | "Show me on the new tax regime" |

The override path applies values via transient attributes on the `User` object (same mechanism we use today for `_chat_risk_score_override`), and `goal_allocation_input_builder` reads each one before constructing `AllocationInput`. Anything outside this allow-list (e.g., goal mutations, custom market scores) falls through to `redirect`.

**Mode definitions:**

- `narrate` — explain the persisted snapshot in plain language ("why is equity 40%?", "is this too aggressive?")
- `educate` — answer an educational question grounded in the snapshot ("what does multi-cap mean?", "why does my plan use arbitrage funds?", "how does the tax treatment work for these funds?"). Like `narrate` but the focus is teaching a concept rather than critiquing the mix.
- `counterfactual_explore` — engine with overrides, NO persist. "What if my risk were 7?" Returns a hypothetical clearly marked as such.
- `clarify` — compose a question back when the user signals direction without value ("I can take more risk" → "What risk score feels right?").
- `recompute_full` — engine fresh with current saved inputs, persist as new plan. "Redo this." or "Run my plan again with my latest profile."
- `recompute_with_overrides` — engine with overrides applied, persist as new plan. "Lock in risk 7." or "Update my plan with ₹1 crore corpus."
- `redirect` — templated Profile-redirect when user wants something out of scope (mutate specific fund picks, change goals, etc.).

### 2. Slimmed: `app/services/ai_bridge/asset_allocation_service.py`

Stays mostly as-is — it's the pure engine wrapper layer. Both the chat module AND the standalone HTTP endpoint consume from here.

**Keeps:**
- `compute_allocation_result` — the engine pipeline call
- `AllocationRunOutcome` dataclass
- `_invoke_pipeline` and pipeline orchestration
- AgentRun persistence (called from inside `compute_allocation_result` after the engine succeeds — same as today)
- `format_allocation_chat_brief` — the chat-brief formatter. Stays here because both the chat module AND the standalone HTTP endpoint need it. The chat module imports and calls it.
- `generate_portfolio_optimisation_response` — the standalone HTTP entry point. Untouched.
- `_summarize_step` and trace helpers — used by the engine path; stay.

**No deletions in this file.** The split is clean: this module owns the engine + presentation primitives. The chat module owns the chat orchestration + decision-making and consumes from here.

### 3. Deleted: `asset_allocation_followup.py` + `asset_allocation_followup_counterfactual.py`

Their behavior moves into `asset_allocation_chat.py`. The counterfactual path becomes one of the modes in `_dispatch_action`. The narrate / clarify / redirect paths become other modes.

### 4. Updated: `app/services/chat_core/brain.py`

Strip the routing rule. Replace the entire `portfolio_optimisation`/`goal_planning` branch with:

```python
if intent_value in ("portfolio_optimisation", "goal_planning"):
    # Local import — handler self-registers via @register decorator at import time.
    from app.services.ai_bridge import asset_allocation_chat  # noqa: F401
    from app.services.ai_bridge.chat_dispatcher import dispatch_chat
    result = await dispatch_chat(intent_value, turn_context)
    return await finalize(
        result.text,
        ideal_allocation_snapshot_id=result.snapshot_id,
        ideal_allocation_rebalancing_id=result.rebalancing_recommendation_id,
    )
```

The `_answer_portfolio_style` helper method is **deleted** entirely. Its logic moves into `asset_allocation_chat.py`'s first-turn branch.

The `wants_fresh_recomputation` consumption path is removed.

### 5. Renamed: `followup_dispatcher.py` → `chat_dispatcher.py`

Same registry pattern, better-named for what it actually does. `register(intent)` and `dispatch_chat(intent, turn_context) -> ChatHandlerResult`.

The signature changes from `dispatch_followup(intent, agent_run, turn_context) -> str` to `dispatch_chat(intent, turn_context) -> ChatHandlerResult`. The handler no longer receives `agent_run` directly — it pulls from `turn_context.last_agent_runs` itself, since first-turn handlers don't have an agent run to begin with.

### 6. Updated: `AI_Agents/src/intent_classifier/`

- `models.py`: drop `wants_fresh_recomputation` from `ClassificationResult`
- `classifier.py`: drop the field from `_LLMOutput` and remove the forwarding in `classify()`
- `prompts.py`: drop the entire `## Recomputation Detection` section. The classifier's job is now just intent + is_follow_up + reasoning.

### 7. Tests reshaped

- New: `app/services/ai_bridge/tests/test_asset_allocation_chat.py` — covers all 6 modes + first-turn engine path
- Deleted: `test_asset_allocation_followup.py` and the counterfactual sibling test
- Updated: `test_followup_dispatcher.py` → `test_chat_dispatcher.py` — simpler signature
- Updated: `AI_Agents/tests/test_intent_classifier.py` — drop `wants_fresh_recomputation` test cases
- Kept: brain tests, TurnContext tests, AgentRun persistence tests

## Data flow walkthroughs

### Scenario 1: First turn — fresh allocation

```
User: "Help me plan for retirement"
  brain → classify → intent=goal_planning, is_follow_up=False
  brain → build_turn_context → last_agent_runs={}
  brain → dispatch_chat("goal_planning", ctx)
  asset_allocation_chat.handle(ctx)
    last_alloc = None → _first_turn_run_engine(ctx)
      compute_allocation_result(...) → outcome
      format_chat_brief(outcome) → text
      record_ai_module_run(...) → persist AgentRun
      return ChatHandlerResult(text, snapshot_id, rec_id)
  brain.finalize(...)
  ➜ user gets full allocation deck
```

### Scenario 2: Follow-up narration

```
User: "Is this too aggressive?"
  brain → classify → intent=goal_planning, is_follow_up=True
  brain → build_turn_context → last_agent_runs={goal_based_allocation: <Turn 1 row>}
  brain → dispatch_chat → asset_allocation_chat.handle(ctx)
    last_alloc exists → _detect_action(ctx)
      Haiku decides: mode="narrate"
    _dispatch_action(narrate)
      narrate prompt with snapshot + question + history
      ➜ narrative text
  brain.finalize(text)  # no new IDs (no engine ran)
```

### Scenario 3: Vague preference signal

```
User: "I can take more risk"
  brain → classify → intent=portfolio_optimisation, is_follow_up=True
  brain → dispatch_chat → asset_allocation_chat.handle(ctx)
    last_alloc exists → _detect_action(ctx)
      Haiku decides: mode="clarify"
                     clarification_question="Sure — what risk score (1-10) feels right? You're at 5.5 today."
    _dispatch_action(clarify) → returns the clarification_question text
  brain.finalize(text)
```

### Scenario 4: Counterfactual exploration

```
User: "What if my risk score were 7?"
  brain → classify → intent=portfolio_optimisation, is_follow_up=True
  brain → dispatch_chat → asset_allocation_chat.handle(ctx)
    last_alloc exists → _detect_action(ctx)
      Haiku decides: mode="counterfactual_explore", overrides={"effective_risk_score": 7.0}
    _dispatch_action(counterfactual_explore)
      apply override to user, compute_allocation_result(db=None, persist=False)
      narrate as hypothetical
      ➜ "Hypothetical at risk 7 (not your saved plan): ..."
  brain.finalize(text)  # no new persist
```

### Scenario 5: Explicit recompute with new constraint

```
User: "Lock in risk 7 as my new plan"
  brain → classify → intent=portfolio_optimisation, is_follow_up=True
  brain → dispatch_chat → asset_allocation_chat.handle(ctx)
    last_alloc exists → _detect_action(ctx)
      Haiku decides: mode="recompute_with_overrides", overrides={"effective_risk_score": 7.0}
    _dispatch_action(recompute_with_overrides)
      apply override to user, compute_allocation_result(persist=True)
      format_chat_brief, persist AgentRun
      ➜ "Updated your plan at risk 7. New mix: ..."
  brain.finalize(text, snapshot_id, rec_id)
```

### Scenario 6: Mutation request

```
User: "Swap arbitrage for liquid funds"
  brain → classify → intent=portfolio_optimisation, is_follow_up=True
  brain → dispatch_chat → asset_allocation_chat.handle(ctx)
    last_alloc exists → _detect_action(ctx)
      Haiku decides: mode="redirect", redirect_reason="change holdings"
    _dispatch_action(redirect)
      ➜ templated "head to your Profile section..."
  brain.finalize(text)
```

### Scenario 7: Educational question

```
User: "What does multi-cap mean and why is it in my plan?"
  brain → classify → intent=portfolio_optimisation, is_follow_up=True
  brain → dispatch_chat → asset_allocation_chat.handle(ctx)
    last_alloc exists → _detect_action(ctx)
      Haiku decides: mode="educate"
    _dispatch_action(educate)
      educate prompt: snapshot + question + "explain the concept and tie it back
        to the user's specific holding"
      ➜ "Multi-cap funds invest across large/mid/small-cap stocks. Your plan has
        ICICI Multicap (₹24.7L) — used here because at risk score 5.4 with a
        24-year retirement horizon, multi-cap gives you breadth without
        concentration in any one segment..."
  brain.finalize(text)  # no engine, no persist
```

## Mode reference table

| Mode | Engine call? | Persists? | Returns |
|---|---|---|---|
| `narrate` | No | No | Critique/explanation of the existing mix |
| `educate` | No | No | Educational answer grounded in snapshot specifics |
| `counterfactual_explore` | Yes (db=None) | No | Hypothetical narration with explicit framing |
| `clarify` | No | No | Composed question to ask user back |
| `recompute_full` | Yes (db live) | Yes (new AgentRun + recommendation) | Fresh allocation deck |
| `recompute_with_overrides` | Yes (db live) | Yes (new AgentRun + recommendation) | Updated allocation deck |
| `redirect` | No | No | Templated Profile-redirect text |

## Error handling

- `_detect_action` LLM call fails → fall back to `narrate` mode (best-effort answer from snapshot rather than failing the turn)
- `compute_allocation_result` returns `blocking_message` → return that message to user (e.g., "missing DOB" path)
- `compute_allocation_result` raises → caught by brain's existing try/except → safe recovery path
- Unknown mode in `_dispatch_action` → log warning, return generic "I'm not sure how to help with that — could you rephrase?" message
- All paths preserve at least current behavior; no regressions vs today's state

## Cost analysis

| Per turn | Today | After unification |
|---|---|---|
| First turn | engine (~$0.05–0.15) | engine (~$0.05–0.15) — unchanged |
| Narrate follow-up | detect + narrate (~$0.004) | detect + narrate (~$0.004) — unchanged |
| Counterfactual | detect + engine + narrate (~$0.05) | detect + engine + narrate (~$0.05) — unchanged |
| Clarify | detect (~$0.002) | detect (~$0.002) — unchanged |
| Recompute (NEW path) | n/a — was engine bypass | detect + engine + brief (~$0.06) |
| Engine bypass (was: classifier said wants_fresh_recomputation=true) | engine (~$0.05–0.15) — wasteful when input unchanged | n/a — handled inside chat module deterministically |

Net per-turn cost: roughly the same or lower. The wasteful "engine re-runs with unchanged inputs" failure mode is eliminated.

## Testing strategy

### Unit tests for `asset_allocation_chat.py`

For each mode, a test that mocks `_detect_action` to return that mode and asserts the correct downstream behavior. Plus:

- `test_first_turn_runs_engine_and_persists` — last_agent_runs={} → engine called, AgentRun persisted, ChatHandlerResult has IDs
- `test_followup_with_no_alloc_falls_back_to_first_turn` — last_agent_runs={} but classifier said is_follow_up — handler treats as first turn (no AgentRun = no narration possible)
- `test_detect_action_failure_falls_back_to_narrate` — LLM raises → response is best-effort narration
- `test_narrate_returns_text_no_engine` — narrate path, no engine call, no persist
- `test_educate_returns_text_no_engine` — educate path, no engine call, no persist
- `test_recompute_full_persists_new_AgentRun` — verify new row written + ChatHandlerResult returns new IDs
- `test_recompute_with_overrides_persists_new_AgentRun` — same with override applied (test multiple override keys: risk_score, total_corpus, tax_regime)
- `test_counterfactual_explore_does_not_persist` — db=None, no new row
- `test_counterfactual_with_invalid_override_falls_to_redirect` — override key not in allow-list → redirect text
- `test_clarify_returns_composed_question` — handler returns the question text
- `test_redirect_returns_template_with_reason` — handler returns the template

### Override allow-list tests

Each new override key needs:
- `test_builder_honors_<key>_override` in `test_goal_allocation_input_builder.py` — verifies the builder reads the transient attribute and uses it in `AllocationInput`
- A unit test in `test_asset_allocation_chat.py` exercising the chat module dispatch with that key

### Brain integration test

- `test_brain_dispatches_to_chat_module_on_portfolio_intent` — mock dispatch_chat, assert called with right args
- `test_brain_returns_handler_result_ids` — handler returns snapshot_id + rec_id, brain forwards to finalize

### Classifier test cleanup

- Remove `WantsFreshRecomputationFieldTests` from intent_classifier test suite

### Manual smoke

After deploy, run the 6 scenarios above in the chat UI. Each should produce the documented behavior.

## Out of scope

- Migrating other intents (market, portfolio_query, general_chat) to unified-chat-module pattern. They don't have AgentRun persistence yet; defer until each is touched for unrelated reasons.
- Stateful multi-turn clarification (bot asks "what value?" → user "7" → bot remembers and applies override). Each turn is independent for now; a vague-then-specific sequence works because turn 2's "7" gets classified as a counterfactual or recompute on its own merits.
- Cross-session memory.
- Adding educational content modes ("explain what multi-cap means in general"). Today this routes to general_chat; if we want it inside the allocation chat module later, add an `educate` mode.
- Tool-using agent loop.

## Implementation sequence

One PR with internal commit sequence (rip-and-replace, not deployed in prod, no incremental-migration concerns):

1. **New `chat_dispatcher.py`** (rename from followup_dispatcher.py) with new signature `dispatch_chat(intent, turn_context) -> ChatHandlerResult`
2. **New `asset_allocation_chat.py`** — full implementation with all 6 modes + first-turn engine path
3. **Brain refactor** — strip `_answer_portfolio_style`, replace portfolio_optimisation branch with thin dispatcher call
4. **Slim `asset_allocation_service.py`** — remove chat-brief formatter and trace helpers (or keep if standalone HTTP needs them); keep `compute_allocation_result` pure
5. **Delete `asset_allocation_followup.py` + `asset_allocation_followup_counterfactual.py`** + their tests
6. **Drop `wants_fresh_recomputation`** from intent_classifier (model + LLM schema + prompt + tests)
7. **Tests** — new asset_allocation_chat tests; brain integration test; classifier cleanup

After this lands, the user-facing chat works end-to-end through the unified module. Old followup files are gone. Brain is dramatically simpler.

## Rollback strategy

If problems emerge after merge: `git revert` the PR. Old behavior returns. Since there are no DB schema changes (we're keeping the AgentRun columns from the previous spec), revert is clean — no migration to undo.

## Open questions for review

- Should `_detect_action` in the chat module use prompt caching aggressively (the system prompt is stable across all turns)? Lean: yes — same pattern as today's followup, mark `cache_control: ephemeral` on the system block.
- Should we keep the `chat_dispatcher` registry pattern given there's only one handler today? Lean: yes — when rebalancing lands it'll register itself. Trivially thin dispatcher now avoids a refactor later.
- Should `recompute_full` and `recompute_with_overrides` be combined into a single `recompute` mode where overrides default to empty dict? Lean: keep separate — distinct semantics make telemetry and reasoning clearer; trivial code overhead.
