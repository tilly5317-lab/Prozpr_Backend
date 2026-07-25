# Tailored Chat Output via Shared Answer Formatter — design

**Date:** 2026-05-01
**Status:** Design
**Owner:** Amoul
**Builds on:** `2026-04-28-unified-chat-modules-design.md` (the per-intent dispatcher pattern). This spec adds a question-aware answer-formatter LLM step to the per-module handlers introduced there.

## Summary

Today, every customer question routed to `asset_allocation` produces a similarly-shaped response — a templated brief on the first turn, and a generic narrate-prompt LLM call on follow-ups. The narrate prompt is essentially "explain the snapshot," so it gravitates to platitudes ("balances safety with growth") regardless of what the user actually asked. The `rebalancing` module has the same problem in its first-turn brief.

This redesign inserts a **shared, question-aware answer-formatter LLM** between each module's output and the customer-facing text. Each module exposes a curated *facts pack* (small flat dict, customer-tellable fields only) and a module-specific *body prompt*; the shared formatter combines them with a house-style preamble, the customer's question, the action mode, and recent history into a single Haiku call that produces a tailored answer.

The existing `_detect_action` per-module classifier stays — it decides whether to re-run the module on follow-ups. Only the *generation* layer changes; routing logic is untouched.

## Goals

- Every customer-facing chat turn that today produces templated text or generic-narrate text becomes question-aware.
- One shared formatter LLM call per applicable turn — same code path for first-turn briefs and follow-up narrations.
- House-style rules (Prozpr voice, length, hedging, prohibition on fund recommendations, prohibition on inventing numbers) live in **one** place.
- Each module's formatter behavior is shaped by a small body prompt and a facts pack that the module owns.
- Mechanical revertibility: if the formatter fails or proves bad, the renamed templated function (`build_fallback_brief`) is the existing safety net.
- Telemetry on formatter invocation rate, success rate, latency, and per-mode usage, captured on `ChatAiModuleRun`.

## Non-goals (follow-ups, not in this spec)

- Manual eval workbook design + scoring rubric for measuring answer quality. Captured as a separate workstream; should run before declaring rollout complete but does not block the build.
- Streaming formatter output to the frontend. Backend-only change here.
- Migrating `portfolio_query` / `general_chat` / `market_commentary` to the formatter. They're already LLM-driven; the urgency is templated/generic-narrate paths.
- Full action-taxonomy for rebalancing (modes + classifier prompt). Defined in the implementation plan, not this spec.
- Prompt-tuning iterations after first ship.

## Locked design decisions

| Decision | Choice | Why |
|---|---|---|
| Scope | Every customer-facing turn that today produces templated/generic-narrate output (asset_allocation, rebalancing) | User intent: tailor to customer's question across the board |
| Action routing | Keep per-module `_detect_action` classifier (handles re-run vs not). Formatter is downstream of it | Re-run-or-not is a real DB-write decision; collapsing into formatter blurs concerns |
| Formatter shape | Hybrid: shared house-style preamble + per-module body prompt | Brand-voice consistency + module-shape specificity |
| Facts-pack envelope | Plain `dict[str, Any]` per module — no enforced top-level keys | Imposing a structure homogenizes outputs (the very failure we're fixing) |
| Modes that bypass formatter | `clarify`, `redirect` — they have deterministic text and no module output to format | Formatter has no useful job in these modes |
| Cost/latency | Non-streaming. One additional Haiku call per turn (3-4 total per chat turn) | Streaming is a separate workstream; latency budget is acceptable |
| Failure handling | LLM failure → `build_fallback_brief(output)` (today's templated function, renamed) | Always-usable answer; no outage class |
| Telemetry | Extend `ChatAiModuleRun` with formatter columns; no sidecar table | Per-turn picture in one row; query-friendly |
| Logging | `logger.error("formatter_failed", extra={"error_class": type(e).__name__, "module": ..., "mode": ...})`. No stack trace, no facts pack in logs | PII risk in facts pack; class name + module + mode is enough to triage |
| Module rollout | asset_allocation **and** rebalancing in this spec (in that order); other modules deferred | User direction; scope-limited |
| Feature flag | None — fallback path *is* today's behavior | A flag-off state is functionally identical to a formatter failure; no value |

## Architecture overview

```
user question
  │
  ▼
intent_classifier (existing, unchanged)
  │
  ▼
chat_dispatcher routes to per-module @register handler (existing)
  │
  ▼
┌───────────────────────────────────────────────┐
│ Per-module handler:                            │
│   if first turn for this module:               │
│     run module → output                        │
│     action_mode = "compute"                    │
│   else:                                         │
│     action_classifier(question, history,        │
│                       last_output)              │
│       → action_mode (module-specific taxonomy)  │
│     if action_mode requires re-run:             │
│       run module → output                      │
│                                                 │
│   if action_mode in {clarify, redirect}:        │
│     # bypass formatter — text is deterministic  │
│     return ChatHandlerResult(...)               │
│                                                 │
│   facts_pack = module.build_facts_pack(out)     │
│   try:                                          │
│     text = answer_formatter.format_answer(      │
│       question=q, action_mode=mode,             │
│       module_name=..., facts_pack=facts_pack,   │
│       body_prompt=_MODULE_FORMATTER_BODY,       │
│       history=h, profile=p,                     │
│     )                                           │
│   except FormatterFailure:                      │
│     text = build_fallback_brief(out)            │
└───────────────────────────────────────────────┘
  │
  ▼
ChatHandlerResult(text=...) returned to ChatBrain → frontend
```

`chat_dispatcher.py` is unchanged. Per-module handlers are rewired but their public signature stays the same.

## Components & file layout

### New shared package

```
app/services/ai_bridge/answer_formatter/
  __init__.py        # re-exports format_answer, FactsPack, FormatterFailure
  formatter.py       # everything: format_answer LLM call,
                     # FORMATTER_HOUSE_STYLE preamble,
                     # FactsPack TypeAlias = dict[str, Any],
                     # ActionMode Literal,
                     # FormatterFailure exception class
  tests/
    test_formatter.py
```

One source file. Collapsed from an earlier sketch to minimize new files.

### Per-module additions

Both `asset_allocation/` and `rebalancing/` follow the same pattern:

```
app/services/ai_bridge/<module>/
  service.py     # CHANGED:
                 #   + add build_<module>_facts_pack(output) -> dict
                 #   - rename format_<module>_chat_brief → build_fallback_brief
                 #     (update all import sites)
  chat.py        # CHANGED:
                 #   + add _<MODULE>_FORMATTER_BODY constant
                 #   - delete _NARRATE_SYSTEM / _EDUCATE_SYSTEM /
                 #     _COUNTERFACTUAL_NARRATE_SYSTEM (asset_allocation)
                 #   - rewire narrate / educate / clarify / redirect /
                 #     recompute / counterfactual paths to call
                 #     answer_formatter.format_answer(...)
                 #   + (rebalancing only) add _detect_rebal_action classifier
  tests/
    test_chat.py     # CHANGED — see Testing section
    test_service.py  # NEW — facts-pack tests
```

**Net new files for the whole feature:**
- 3 in shared package (`__init__.py`, `formatter.py`, `tests/test_formatter.py`)
- 1 per module (`tests/test_service.py` × 2 modules = 2)

**Untouched:** `chat_dispatcher.py`, `intent_classifier_service.py`, `general_chat_service.py`, `portfolio_query_service.py`, `market_commentary_service.py`.

## Per-module API contract

### Action classifier

Each module's `chat.py` exposes:

```python
async def _detect_<module>_action(
    question: str,
    last_run: AgentRunRecord | None,
    history: list[dict],
) -> ChatAction
```

Where `ChatAction` (existing in asset_allocation today) carries:

```python
@dataclass
class ChatAction:
    mode: str                                # module-specific taxonomy
    overrides: dict | None = None            # for recompute_with_overrides
    clarification_question: str | None = None
    redirect_reason: str | None = None
```

Re-run vs not is encoded **implicitly** via mode-name → re-run mapping inside each module's handler. No explicit `requires_rerun` field.

- **asset_allocation:** existing `_detect_action` taxonomy retained (`narrate`, `educate`, `clarify`, `redirect`, `recompute_full`, `recompute_with_overrides`, `counterfactual_explore`).
- **rebalancing:** new `_detect_rebal_action`. Spec mandates at minimum `narrate`, `recompute`, `clarify`, `redirect`. Full taxonomy + classifier prompt nailed down in the implementation plan.
- **First-turn:** classifier is **not called**. Handler implicitly proceeds with `mode="compute"`, runs the module, calls the formatter.

### Facts pack

Each module's `service.py` exposes:

```python
def build_<module>_facts_pack(output: <ModuleOutputType>) -> dict[str, Any]
```

Constraints:
- ≤ ~1500 tokens when JSON-serialized (validated in tests).
- Customer-tellable fields only — no internal subgroup keys, no fund/ISIN, no SEBI sub-categories.
- Pure function of module output (no DB calls, no I/O, no time-dependent values).
- Plain `dict` — no enforced envelope (`headline / sections / notes` was rejected to avoid homogenizing outputs).

**Example — asset_allocation facts-pack sketch (illustrative; final shape lives in code):**

```python
{
    "risk_score": 5.5,
    "age": 39,
    "total_corpus_inr": 8_000_000,
    "asset_class_mix_pct": {"equity": 40.2, "debt": 51.0, "others": 8.8},
    "by_horizon": [
        {"horizon": "emergency",   "amount_inr": 300_000,  "mix_pct": {"equity": 0,   "debt": 100, "others": 0}},
        {"horizon": "short_term",  "amount_inr": 700_000,  "mix_pct": {"equity": 0,   "debt": 100, "others": 0}},
        {"horizon": "medium_term", "amount_inr": 2_500_000, "mix_pct": {"equity": 50,  "debt": 50,  "others": 0}},
        {"horizon": "long_term",   "amount_inr": 4_500_000, "mix_pct": {"equity": 60,  "debt": 25,  "others": 15}},
    ],
    "goals": [
        {"name": "Retirement", "amount_needed_inr": 50_000_000, "horizon_months": 300, "bucket": "long_term", "shortfall_inr": None, "rationale": "<from rationale_llm>"},
    ],
    "future_investments": [
        {"horizon": "long_term", "monthly_inr": 100_000, "purpose": "fill retirement gap"},
    ],
    "context": [
        "All amounts in multiples of ₹100.",
        "Equity allocation respects Phase-1 risk-bound 30-60% for risk score 5.5.",
    ],
}
```

The shared `FORMATTER_HOUSE_STYLE` preamble instructs the LLM: *"Cite numbers only from the facts pack. If a number isn't present, don't invent it. Let the customer's question shape the response — do not default to a fixed rendering order."*

### Module-specific body prompt

Each `chat.py` defines `_<MODULE>_FORMATTER_BODY` — a constant string that documents the facts-pack shape inline, lists module-specific guardrails, and gives mode-specific guidance (e.g. for `recompute_with_overrides`: "highlight the deltas between the saved plan and the new one"). The body prompt is parameterized by `action_mode` at call time so one prompt covers all modes the module's formatter touches.

### What lives where (summary)

| Concern | Lives in |
|---|---|
| Mode taxonomy + classifier prompt | Per-module `chat.py` |
| Facts-pack builder | Per-module `service.py` |
| Module-specific formatter body prompt | Per-module `chat.py` (`_<MODULE>_FORMATTER_BODY`) |
| Shared house-style preamble | `answer_formatter/formatter.py` (`FORMATTER_HOUSE_STYLE`) |
| LLM call mechanics, fallback wiring, telemetry hook | `answer_formatter/formatter.py` |

## Data flow

### A. First turn, asset_allocation
1. `chat_dispatcher.dispatch_chat` routes to AA handler.
2. Handler sees no `last_agent_runs["asset_allocation"]` → first-turn path.
3. `compute_allocation_result(user, question, persist=True, ...)` runs full pipeline + persists.
4. `action_mode = "compute"` (implicit; no `_detect_action` call).
5. `facts_pack = build_aa_facts_pack(outcome.result)`.
6. `text = answer_formatter.format_answer(question=q, action_mode="compute", module_name="asset_allocation", facts_pack=..., body_prompt=_AA_FORMATTER_BODY, history=h, profile=p)`.
7. On success: `text` → `ChatHandlerResult`. On `FormatterFailure`: `text = build_fallback_brief(outcome.result)`.

### B. Follow-up turn, narrate path
1. AA handler sees `last_agent_runs["asset_allocation"]` is set → follow-up path.
2. `_detect_action(...)` → `ChatAction(mode="narrate")`.
3. No re-run. Use `last_alloc.output_payload["allocation_result"]` rehydrated as the source.
4. `facts_pack = build_aa_facts_pack(rehydrated_output)`.
5. `text = answer_formatter.format_answer(action_mode="narrate", facts_pack=..., ...)`.
6. Success/failure: same as A.

### C. Follow-up turn, recompute_with_overrides
1. Handler → `_detect_action` → `mode="recompute_with_overrides", overrides={"effective_risk_score": 7.0}`.
2. Re-run: `compute_allocation_result(user_with_override, persist=True, ...)`.
3. `facts_pack = build_aa_facts_pack(new_outcome.result)`.
4. `text = answer_formatter.format_answer(action_mode="recompute_with_overrides", facts_pack=..., ...)` — body prompt steers toward delta-explanation.
5. Same fallback rules.

### D. Follow-up turn, clarify path
1. `_detect_action` → `mode="clarify", clarification_question="Did you mean X or Y?"`.
2. **No facts pack, no formatter call.** Path returns the clarification text directly.
3. Same for `redirect`: deterministic template, no formatter.

The formatter runs in 5 of the 7 AA modes (`compute`, `narrate`, `educate`, `recompute_full`, `recompute_with_overrides`, `counterfactual_explore`) and is bypassed in `clarify` / `redirect`.

### E. First turn, rebalancing
Mirror of A against rebalancing's pipeline. Fallback: `build_fallback_rebal_brief(output)` (renamed from `format_rebalancing_chat_brief`).

### F. Follow-up turn, rebalancing
Mirror of B/C against rebalancing's action taxonomy (full taxonomy lives in the implementation plan).

## Error handling, telemetry, persistence

### Failure modes

| Failure | Behavior |
|---|---|
| Formatter LLM timeout / API error | Catch as `FormatterFailure`; call `build_fallback_brief(output)`; user sees deterministic answer |
| Formatter returns empty / malformed text | Same — fallback path |
| Action classifier fails on follow-up | Existing AA fallback (`narrate` mode) preserved; rebalancing gets analogous default |
| `build_facts_pack` raises | Treat as bug; bridge catches, logs at error, falls back. Pure functions of module output — should not happen |
| Module itself fails | Existing handling unchanged — out of scope |

Skeleton:

```python
try:
    facts_pack = build_aa_facts_pack(output)
    text = await answer_formatter.format_answer(
        question=q, action_mode=mode, module_name="asset_allocation",
        facts_pack=facts_pack, body_prompt=_AA_FORMATTER_BODY,
        history=h, profile=p,
    )
except FormatterFailure as e:
    logger.error("formatter_failed", extra={
        "module": "asset_allocation", "mode": mode,
        "error_class": type(e).__name__,
    })
    text = build_fallback_brief(output)
```

### Telemetry

Extend `ChatAiModuleRun` (Alembic migration in same plan) with five nullable columns:

- `formatter_invoked: bool | None` — did we attempt the formatter LLM?
- `formatter_succeeded: bool | None` — did it return usable text?
- `formatter_latency_ms: int | None` — wall time of the formatter call.
- `formatter_error_class: str | None` — exception class on failure.
- `action_mode: str | None` — the action classifier's decision.

No sidecar table. One row per chat turn, queryable without joins.

### Persistence of formatter outputs

Formatter text lands in `ChatMessage.content` via the existing chat router — no new persistence path. Facts packs are not persisted; they're deterministic from the module output, which is already persisted.

## Testing strategy

### Shared `answer_formatter/tests/test_formatter.py`

- Prompt assembly: house-style preamble + body + facts pack + question + action_mode + truncated history all present.
- Token bound: typical prompt < 4000 tokens.
- LLM mocked; no live Anthropic calls.
- Failure paths: timeout, malformed response, empty string each raise `FormatterFailure`.
- House-style sanity: prompt contains the prohibition strings ("never recommend a specific fund", "never invent numbers") so future edits don't accidentally remove them.

### Per-module `tests/test_service.py` (new)

- Facts-pack happy path on representative profiles; expected top-level keys present; no fund/ISIN/sub_category strings leak.
- Token-budget check: `len(json.dumps(facts_pack)) < <module's budget>`.
- Determinism: same input twice → identical facts pack.
- Fallback brief: `build_fallback_brief(output)` returns non-empty markdown.

### Per-module `tests/test_chat.py` (changed)

- Formatter invoked on success path: patch `answer_formatter.format_answer`, assert called with `(action_mode, facts_pack, ...)`.
- Formatter failure → fallback path: patch to raise `FormatterFailure`; assert handler returns `build_fallback_brief(output)` text without raising.
- Clarify / redirect bypass: assert formatter is **not** called.
- Action-mode propagation: classifier-returned mode equals string passed to `format_answer`.
- Telemetry write: `ChatAiModuleRun` row gets `action_mode`, `formatter_invoked`, `formatter_succeeded`, `formatter_latency_ms`, `formatter_error_class` populated correctly on success and on fallback.

### Out of scope (acknowledged gap)

- Automated answer-quality checks. Manual eval workbook is a follow-on workstream.
- Cross-question regression detection ("same canned output" failure mode). Same workbook covers it.

## Rollout & sequencing

1. **Shared `answer_formatter` package + tests** — self-contained, no consumers yet.
2. **Alembic migration** for the five new `ChatAiModuleRun` columns (nullable, additive — safe to ship ahead of code).
3. **Asset_allocation migration:**
   - Add `build_aa_facts_pack` to `service.py`; rename `format_allocation_chat_brief` → `build_fallback_brief`; update import sites.
   - Add `_AA_FORMATTER_BODY` to `chat.py`; delete `_NARRATE_SYSTEM` / `_EDUCATE_SYSTEM` / `_COUNTERFACTUAL_NARRATE_SYSTEM`; rewire all formatter-applicable paths.
   - Wire telemetry writes.
   - Update `tests/test_chat.py`; add `tests/test_service.py`.
4. **Rebalancing migration** — mirror of step 3. Action taxonomy nailed down in the implementation plan.
5. **Manual eval workbook** — separate workstream.

### Deployment posture

- No feature flag. Fallback path is today's templated answer; flag-off would be functionally identical.
- Steps 1–3 ship together (or as a tight stack). Step 2 (Alembic) ships before any code that writes the new columns deploys.
- Step 4 (rebalancing) ships independently after step 3 is live.

### Reversibility

If the AA migration goes badly, revert is a single PR. The renamed `build_fallback_brief` and the deleted `_NARRATE_SYSTEM` / `_EDUCATE_SYSTEM` / `_COUNTERFACTUAL_NARRATE_SYSTEM` are recoverable from the revert. New columns stay populated; nulls in older rows are expected.

## Open questions deferred to implementation plan

- Exact rebalancing action taxonomy + classifier prompt text.
- Final shape of each module's facts pack (sketch above is illustrative).
- House-style preamble wording — first draft in the plan, iterated post-ship.
- Per-module body prompt wording — same.
- Token budget per module's facts pack — measured during implementation.
