# asset_allocation_followup — design

**Date:** 2026-04-27
**Status:** Design
**Owner:** Amoul

## Summary

Today, follow-up questions on a previously-shown allocation ("is this too aggressive?", "why so much arbitrage?") re-run the full goal-based allocation pipeline, producing a fresh allocation instead of answering the question. This is the bug surfaced in the chat session that triggered this design.

We will introduce a small architectural foundation (per-turn `TurnContext`, persisted `AgentRun` records with structured input/output, classifier-driven routing) and a new per-module `asset_allocation_followup` handler that narrates the persisted allocation snapshot rather than recomputing it. The pattern is designed so future modules (rebalancing, market commentary, portfolio query) can adopt it later as a copy-and-tune of the same shape — but only allocation gets wired in this iteration.

## Goals

- Fix the bug: follow-ups on an existing allocation are answered by narrating the snapshot, not by re-running the engine.
- Establish a scalable per-module follow-up pattern: thin shared dispatcher, per-module followup file owning its own prompt and recompute semantics.
- Wire the previously-orphaned classifier signals (`active_intent` in, `is_follow_up` out) and add one new signal (`wants_fresh_recomputation`).
- Support B-scope counterfactuals for allocation only ("what if my risk were 7?") — call the engine with overrides, mark hypothetical, do not persist as the user's plan.

## Non-goals

- Migrating market_commentary / portfolio_query / general_chat to the `AgentRun` + `TurnContext` pattern. Deferred. They keep working as-is.
- Mutation flows (scope C). "Swap arbitrage for liquid" politely redirects in this iteration.
- UI affordances for "save plan / undo plan" — not needed for narration + non-persisted counterfactuals.
- Rebalancing module integration — when rebalancing lands, it ships with its own `rebalancing_followup.py` following this pattern.

## Locked design decisions

| Decision | Choice | Why |
|---|---|---|
| Past-output model | Generic `AgentRun` log | Scales to N agents; new agents inherit follow-up capability via the dispatcher |
| Slice size | MVP — allocation only this iteration | Ships the fix faster; pattern is established for rebalancing to inherit |
| Followup architecture | Per-module followup files + thin dispatcher | Each module owns its full lifecycle (run + followup); domain semantics stay local |
| Storage shape | Extend `chat_ai_module_runs` table | One persistence path; existing table is semantically already an agent-run record |
| Followup scope | B — read-only narration + counterfactual; mutation redirected | Counterfactuals enable "what if" exploration without dragging in plan-mutation UX |
| Stale-snapshot policy | Most-recent `AgentRun` in session, no age cap | Matches user mental model; `wants_fresh_recomputation` covers explicit re-run requests |

## Architecture overview

```
ChatBrain.run_turn(turn)
│
├── classify_user_message(question, history, active_intent)
│       returns: intent, confidence, is_follow_up, wants_fresh_recomputation
│
├── build_turn_context(turn, classification, db)
│       loads: history, last_agent_runs[per_module], active_intent
│
└── dispatch:
    │
    if intent in (portfolio_optimisation, goal_planning):
       │
       if classification.is_follow_up
          AND turn_context.last_agent_runs.get("goal_based_allocation")
          AND NOT classification.wants_fresh_recomputation:
              → followup_dispatcher.dispatch(intent, last_agent_run, turn_context)
              → asset_allocation_followup.handle(...)
       else:
          → existing path: build_ailax_spine → compute_allocation_result
            (now also persists an AgentRun)
    │
    elif other intents:
       → existing behavior, no AgentRun persistence yet (deferred to future iteration)
```

The brain's only structural change for this iteration is the new `if is_follow_up AND ... → followup_dispatcher` branch in the portfolio_optimisation/goal_planning case. Everything else stays identical.

## Components

### 1. Schema migration: extend `chat_ai_module_runs`

Add two nullable JSONB columns to the existing table. Existing telemetry rows (the `chat_flow` per-turn summaries written by `log_chat_turn_flow_summary`) keep being written with these NULL.

```python
# alembic/versions/<hash>_add_payload_columns_to_chat_ai_module_runs.py

def upgrade():
    op.add_column("chat_ai_module_runs",
        sa.Column("input_payload", postgresql.JSONB, nullable=True))
    op.add_column("chat_ai_module_runs",
        sa.Column("output_payload", postgresql.JSONB, nullable=True))

def downgrade():
    op.drop_column("chat_ai_module_runs", "output_payload")
    op.drop_column("chat_ai_module_runs", "input_payload")
```

ORM update in `app/models/chat_ai_module_run.py`:
```python
input_payload:  Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
output_payload: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
```

### 2. Persistence helper

Extend `app/services/ai_module_telemetry.py` `record_ai_module_run` with two new kwargs:
```python
async def record_ai_module_run(
    db, *, user_id, session_id, module, reason,
    intent_detected=None, spine_mode=None, duration_ms=None,
    extra=None, emit_standard_log=True,
    input_payload: dict | None = None,    # NEW
    output_payload: dict | None = None,   # NEW
) -> uuid.UUID:                           # NOW returns row id
```
Returns the new row's id so callers can correlate.

Existing call sites unchanged (they pass nothing, get NULLs). Only the allocation bridge passes the new kwargs in this iteration.

### 3. `TurnContext` builder

New file: `app/services/chat_core/turn_context.py`

```python
@dataclass(frozen=True)
class AgentRunRecord:
    id: uuid.UUID
    module: str
    intent_detected: str | None
    input_payload: dict | None
    output_payload: dict | None
    created_at: datetime

@dataclass(frozen=True)
class TurnContext:
    user_ctx: User
    user_question: str
    conversation_history: list[dict[str, str]]
    client_context: dict | None
    session_id: uuid.UUID
    db: AsyncSession | None
    effective_user_id: uuid.UUID
    last_agent_runs: dict[str, AgentRunRecord]   # keyed by module name
    active_intent: str | None                    # from prior turn's classifier run

async def build_turn_context(turn: ChatTurnInput, db) -> TurnContext:
    """Loads last AgentRun per module for this session + the prior intent."""
    ...
```

`build_turn_context` issues two cheap queries:

1. `SELECT DISTINCT ON (module) * FROM chat_ai_module_runs WHERE session_id = $1 AND output_payload IS NOT NULL ORDER BY module, created_at DESC` — last persisted `AgentRun` per module in this session. Returns a dict keyed by module name. Empty in this iteration except for `goal_based_allocation`, since it's the only agent migrated.
2. `SELECT intent_detected FROM chat_ai_module_runs WHERE session_id = $1 AND intent_detected IS NOT NULL ORDER BY created_at DESC LIMIT 1` — the most-recent intent observed in this session, regardless of module. Source: the existing `chat_flow` telemetry rows already record `intent_detected` for every turn, so this works without migrating the intent_classifier itself to persist `AgentRun`s.

This second query gives us `active_intent` to pass into the classifier without bundling intent_classifier migration into this iteration.

### 4. Classifier extension: `wants_fresh_recomputation`

In `AI_Agents/src/intent_classifier/`:

`models.py` — add field to `ClassificationResult`:
```python
wants_fresh_recomputation: bool = False
```

`prompts.py` — add definition section:
```
## Recomputation Detection

Set wants_fresh_recomputation = true when the customer is explicitly asking
to re-run the analysis with new inputs. Triggers:
- Adds new constraints ("redo this without arbitrage")
- Adds new money / new goals ("I have 10L more, redo")
- Asks for re-execution ("rerun", "redo", "recompute", "let's do this again")

Set wants_fresh_recomputation = false when:
- Asking explanations, critique, "why" / "is this" / "what does X mean"
- Counterfactual ("what if X were Y?") — these are exploratory, not requests to
  change the saved plan; the followup handler routes them appropriately
- Asking for a different mutation ("swap X for Y") — the followup handler will
  redirect; classifier should not pre-trigger a re-run
```

Bridge: `app/services/ai_bridge/intent_classifier_service.py` — extend `classify_user_message` to accept `active_intent` and pass it into `ClassificationInput`. Brain reads `active_intent` from the freshly-built `TurnContext`.

### 5. Brain routing change

In `app/services/chat_core/brain.py`:

```python
# At top of run_turn, after building TurnContext:
turn_context = await build_turn_context(turn, db)

classification = await classify_user_message(
    customer_question=turn.user_question,
    conversation_history=turn.conversation_history,
    active_intent=turn_context.active_intent,
)

# In portfolio_optimisation/goal_planning branch:
if intent_value in ("portfolio_optimisation", "goal_planning"):
    last_alloc = turn_context.last_agent_runs.get("goal_based_allocation")
    is_followup_route = (
        classification.is_follow_up
        and last_alloc is not None
        and not classification.wants_fresh_recomputation
    )
    if is_followup_route:
        text = await dispatch_followup(
            intent=intent_value,
            agent_run=last_alloc,
            turn_context=turn_context,
        )
        return await finalize(text)  # no new snapshot/recommendation IDs
    # else: existing path
    p_content, p_reb, p_snap = await self._answer_portfolio_style(turn, flow)
    return await finalize(p_content, ...)
```

`ChatTurnInput` extended to optionally carry the prebuilt `TurnContext`, OR `_answer_portfolio_style` is updated to take `TurnContext` directly. Decision deferred to PR review — both work.

### 6. Followup dispatcher

New file: `app/services/ai_bridge/followup_dispatcher.py`

```python
from typing import Awaitable, Callable

_HANDLERS: dict[str, Callable[..., Awaitable[str]]] = {}

def register(intent: str):
    def decorator(fn):
        _HANDLERS[intent] = fn
        return fn
    return decorator

async def dispatch_followup(
    intent: str, agent_run: AgentRunRecord, turn_context: TurnContext,
) -> str:
    handler = _HANDLERS.get(intent)
    if handler is None:
        # Should not happen if brain only routes registered intents,
        # but guard for future regressions.
        raise RuntimeError(f"No followup handler registered for intent={intent}")
    return await handler(agent_run, turn_context)
```

Registration happens at import time in each handler module.

### 7. `asset_allocation_followup.py`

New file: `app/services/ai_bridge/asset_allocation_followup.py`

```python
@register("portfolio_optimisation")
@register("goal_planning")
async def handle_allocation_followup(
    agent_run: AgentRunRecord, ctx: TurnContext,
) -> str:
    """
    Read-only narration of an existing allocation snapshot, plus
    non-persisted counterfactuals for 'what if X were Y' patterns.
    """
    # 1. Pull the structured allocation from agent_run.output_payload
    # 2. Detect counterfactual override (LLM call OR small classifier — see below)
    # 3a. If counterfactual: build override AllocationInput, call
    #     compute_allocation_result, narrate as hypothetical
    # 3b. Else: narrate the existing snapshot
```

**Counterfactual detection** (B scope): the responder makes ONE Haiku call with structured output to decide:
```python
class FollowupAction(BaseModel):
    mode: Literal["narrate", "counterfactual", "redirect_mutation"]
    counterfactual_overrides: dict | None  # e.g., {"effective_risk_score": 7}
    redirect_reason: str | None            # e.g., "user wants to change holdings"
```
Then dispatches:
- `narrate` → narration prompt with the existing `output_payload`
- `counterfactual` → call `compute_allocation_result` with overrides applied to `AllocationInput`, narrate the result with explicit "this is hypothetical" framing, **do not persist a new AgentRun**
- `redirect_mutation` → templated response pointing user to Profile / Goals UI

**Allowed counterfactual overrides for this iteration:** `effective_risk_score` only. Other overrides (goal amounts, timelines, contribution rates) are deferred. Anything outside the allow-list falls through to `redirect_mutation`.

**Persistence rules:**
- Narration: no new AgentRun (we read, we don't run).
- Counterfactual: no new AgentRun (it's not the user's plan; persisting would let the next follow-up narrate the hypothetical instead of the real plan).
- Redirect: no new AgentRun.

The narration prompt receives:
- The persisted `AllocationResult` JSON (asset class %, fund picks, goal slices, future_investments)
- Recent conversation history (last 6-10 turns, prompt-cached)
- The user's age, risk score, and goals from `ctx.user_ctx`
- The new question

Model: Claude Haiku 4.5 with prompt caching on the snapshot + user profile blocks (these are stable across the full session).

### 8. `asset_allocation_service.py` — persist `AgentRun`

In `app/services/ai_bridge/asset_allocation_service.py`, after `compute_allocation_result` runs successfully (and after `allocation_recommendation_persist` writes the domain rows), write an `AgentRun` row:

```python
await record_ai_module_run(
    db,
    user_id=user_id,
    session_id=chat_session_id,
    module="goal_based_allocation",
    reason="full_pipeline_run",
    intent_detected=intent,
    duration_ms=ms,
    input_payload=allocation_input.model_dump(mode="json"),
    output_payload={
        "allocation_result": allocation_result.model_dump(mode="json"),
        "chat_brief": chat_brief_text,
        "correlation_ids": {
            "snapshot_id": str(snapshot_id) if snapshot_id else None,
            "rebalancing_recommendation_id": str(rec_id) if rec_id else None,
        },
    },
)
```

This is additive — `allocation_recommendation_persist.py` continues writing the domain tables unchanged. The new row is just the structured I/O record for follow-up reasoning.

## Data flow walkthrough

### Scenario 1: narration

```
Turn 1 (user): "I want to plan for retirement"
  brain → classify (intent=portfolio_optimisation, is_follow_up=false)
  build_turn_context: last_agent_runs={} (empty session)
  routing: not is_follow_up → existing path → compute_allocation_result
  asset_allocation_service: persists AgentRun (output_payload contains snapshot)
  reply: full allocation brief

Turn 2 (user): "is this too aggressive?"
  brain → classify (intent=portfolio_optimisation, is_follow_up=true,
                    wants_fresh_recomputation=false, active_intent=portfolio_optimisation)
  build_turn_context: last_agent_runs={goal_based_allocation: <Turn 1 row>}
  routing: is_follow_up AND last_run AND NOT wants_fresh → followup_dispatcher
  dispatch_followup → handle_allocation_followup
  detect_action: mode=narrate
  Haiku narration call with snapshot + question
  reply: narrative explanation
  no new AgentRun persisted
```

### Scenario 2: counterfactual

```
Turn 3 (user): "what if my risk were 7?"
  brain → classify (intent=portfolio_optimisation, is_follow_up=true,
                    wants_fresh_recomputation=false)
  build_turn_context: last_agent_runs={goal_based_allocation: <Turn 1 row>}
  routing: is_follow_up AND last_run AND NOT wants_fresh → followup_dispatcher
  dispatch_followup → handle_allocation_followup
  detect_action: mode=counterfactual, overrides={"effective_risk_score": 7}
  build override AllocationInput from Turn 1's input_payload + overrides
  call compute_allocation_result (no persistence)
  narrate result with "hypothetical, not your saved plan" framing
  reply
  no new AgentRun persisted
```

### Scenario 3: explicit recompute

```
Turn 4 (user): "actually I have 10L more, redo this"
  brain → classify (intent=portfolio_optimisation, is_follow_up=true,
                    wants_fresh_recomputation=true)
  routing: wants_fresh_recomputation → bypass followup, fall to existing path
  compute_allocation_result runs → persists new AgentRun (now most-recent)
  reply: new allocation brief
```

### Scenario 4: mutation request

```
Turn 5 (user): "swap arbitrage for liquid"
  brain → classify (is_follow_up=true, wants_fresh_recomputation=false)
  routing: → followup_dispatcher → handle_allocation_followup
  detect_action: mode=redirect_mutation, reason="user wants to change holdings"
  reply: templated redirect to Profile / re-questionnaire
```

## Error handling

- `build_turn_context` failure → log, fall through to existing path with empty `last_agent_runs`. Bug fix is degraded to "no follow-up routing" but chat still works.
- Followup detect_action LLM call failure → fall through to existing allocation path (engine re-run). Worst case = current bug behavior, not worse.
- Counterfactual `compute_allocation_result` failure → fall back to narration of existing snapshot with note that the hypothetical couldn't be computed.
- Empty / corrupted `output_payload` on the AgentRun → treat as if no last run exists; existing path runs.
- `record_ai_module_run` failure on the allocation persistence step → log, continue (the user gets their reply; we lose the follow-up capability for that turn's snapshot).

All failure modes preserve at least current behavior.

## Cost analysis

**Net per-turn LLM cost: down**, driven by avoiding redundant pipeline reruns on follow-ups.

- Narration follow-up: ~$0.002/turn (Haiku, with prompt caching on snapshot + profile)
- Counterfactual follow-up: ~$0.05/turn (real engine call + narration)
- Explicit-recompute / first-turn allocation: ~$0.05–0.15/turn (unchanged from today)

Compared to today's behavior where every follow-up costs the full ~$0.05–0.15/turn, the dominant traffic shape (narration follow-ups) drops 20–50×. Counterfactual cost is roughly the same as today's misrouted re-run. Explicit-recompute is unchanged.

Storage: one extra row per agent invocation (currently only allocation in this iteration). At expected traffic, sub-GB per year.

## Testing strategy

### Unit tests

- `tests/ai_bridge/test_followup_dispatcher.py` — registration, lookup, missing-handler error
- `tests/ai_bridge/test_asset_allocation_followup.py`
  - narrate path with synthetic agent_run
  - counterfactual path with risk_score override (mock `compute_allocation_result`)
  - mutation redirect path
  - failure-fallback path
- `tests/ai_bridge/test_asset_allocation_service.py` — extend with assertion that `AgentRun` row is written with `output_payload` populated (uses sqlite test fixture)
- `tests/intent_classifier/test_classification.py` — assert `wants_fresh_recomputation=true` for "redo with X" prompts and `false` for "is this too aggressive"
- `tests/chat_core/test_turn_context.py` — DISTINCT ON query returns one row per module, ordered correctly; `active_intent` derived from latest classifier run

### Integration tests

- `tests/integration/test_chat_followup_flow.py`
  - Turn 1: ask for allocation → AgentRun persisted with `output_payload`
  - Turn 2: "is this too aggressive?" → routes to followup, no engine call (mocked & asserted not called)
  - Turn 3: "redo with risk 7" → routes through engine, new AgentRun
  - Turn 4: counterfactual "what if risk were 7" → engine called once (counterfactual), no new AgentRun

### Manual smoke

- Hit the running server with the bug-reproducing prompt sequence; verify the Turn 2 response is narrative explanation (not a fresh allocation).

## Out of scope (explicit)

- `market_commentary_followup`, `portfolio_query_followup`, `general_chat_followup`. Pattern is established; they migrate when their owning module gets touched for any reason.
- Rebalancing module integration. Will ship with its own `rebalancing_followup.py` paired with `rebalancing_service.py`.
- Counterfactual overrides beyond `effective_risk_score`. Goal amounts, timelines, and contributions are deferred to a future iteration once telemetry shows demand.
- Mutation flows (scope C). User-driven plan changes via chat ("swap X for Y") wait for UI affordances around save/undo.
- Stale-snapshot caps (N-turn / N-minute). Most-recent-in-session is sufficient until telemetry suggests otherwise.

## Implementation sequence

One PR. The work is still sequenced as a series of logical commits within the PR — each commit independently builds and passes its own tests, so the diff is reviewable in slices even though it merges as a single unit.

**Commit sequence inside the PR:**

1. **Schema & persistence helper.** Alembic migration adding `input_payload` + `output_payload` columns. ORM model update. `record_ai_module_run` accepts new kwargs and returns id. No behavior change.
2. **Allocation persists `AgentRun`.** `asset_allocation_service.py` writes the new row after pipeline completes. Visible only in DB; no chat behavior change.
3. **Classifier extension.** Add `wants_fresh_recomputation` to `ClassificationResult`. Update prompt with rules. Bridge passes `active_intent` through. Unit tests.
4. **`TurnContext` + brain wiring.** Build TurnContext, pass `active_intent` to classifier, read `is_follow_up` and `wants_fresh_recomputation`. No new routing yet — wired through and logged.
5. **Dispatcher + `asset_allocation_followup` + brain routing.** Add `followup_dispatcher.py` and `asset_allocation_followup.py` (detect_action + narrate + counterfactual + redirect). Brain routes follow-ups to it. **This commit fixes the bug.**
6. **Integration tests & telemetry asserts.** End-to-end flow tests covering the four data-flow scenarios. CLAUDE.md updates kept local per project memory.

**Why one PR rather than six:**
- Solo developer — no team review benefit from splitting.
- Single coherent feature — partial deploys add no user value (commits 1–4 are invisible plumbing on their own).
- Faster path to the user-visible bug fix.
- Internal commits still preserve atomic reviewability and clean revert points if needed.

**Rollback strategy:** if the merged PR causes issues, revert the whole PR. The Alembic migration includes a working `downgrade()` so the schema rolls back cleanly with the code.

## Open questions for review

- Should `ChatTurnInput` carry the prebuilt `TurnContext`, or should we build it fresh in `ChatBrain.run_turn`? Current design builds in-brain. Trivial to flip if router-level building turns out cleaner.
- Should we add an index on `(session_id, module, created_at DESC) WHERE output_payload IS NOT NULL` for the TurnContext query? Yes if traffic warrants; defer to PR 1 review.
- Should counterfactual results be cached per-session (so "what if risk were 7?" asked twice doesn't re-run the engine)? Probably yes via a TTL'd in-memory cache; add to PR 5 if straightforward.
