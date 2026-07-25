# Rebalancing → Chat Integration — Design

Date: 2026-04-28
Status: Approved (pending user review of this spec)

## 1. Goal

Integrate the existing portfolio rebalancing engine at `AI_Agents/src/Rebalancing/` into the
chat system so that a user asking "rebalance my portfolio" gets a sectioned-markdown trade
plan in chat, with the structured response persisted for the frontend to render rich cards.

The rebalancing engine itself is untouched. All new code is the bridge that feeds it inputs
and renders its outputs.

## 2. Key decisions

| Question | Decision |
|---|---|
| Routing — new top-level intent or sub-mode of allocation? | **New top-level intent `REBALANCING`.** Cleanest separation; reconsider merging later if needed. |
| When does rebalancing run? | **Only when the user asks for it.** Allocation alone runs for "what's my ideal allocation"-class questions. |
| Where does the target allocation come from? | **Cache-first with 90-day TTL.** Reuse the most recent allocation row if ≤ 90 days old; otherwise re-run allocation inline as part of the same chat turn. |
| What does the chat reply look like? | **Deterministic sectioned markdown built in pure Python** + structured response persisted in DB. Mirrors the asset-allocation precedent. No LLM narration step. |
| What input does rebalancing take from allocation? | **Sub-asset-group level INR targets only** (`aggregated_subgroups[*].(subgroup, total)`). Fund-level distribution is the engine's job (step 1: cap-and-spill). |
| Where does total corpus come from? | **Sum of current mutual-fund holdings from DB** (market value). Not from allocation's `grand_total`. |
| Where does the fund-rank table come from? | **Static CSV at `AI_Agents/Reference_docs/Prozpr_fund_ranking.csv`** (173 rows; columns `asset_subgroup, sub_category, rank, isin, recommended_fund`). Loaded at module import. |

## 3. Architecture

The AI_bridge layer is the only thing that calls into AI_Agents. The rebalancing engine
runs unmodified.

```
ChatBrain.run_turn  (app/services/chat_core/brain.py)
   │
   ▼
dispatch_chat("rebalancing")
   │
   ▼
rebalancing/chat.py            ── new, AI_bridge layer
   │
   ▼
rebalancing/service.py         ── new, AI_bridge layer
   │
   ├── on cache miss: compute_allocation_result  (existing)
   │       └─► AI_Agents/src/goal_based_allocation_pydantic/pipeline.py
   │              └─► run_allocation_with_state(...)
   │
   ├── build_rebalancing_input_for_user           ── new, AI_bridge layer
   │       └─► reads AI_Agents/Reference_docs/Prozpr_fund_ranking.csv
   │
   └─► AI_Agents/src/Rebalancing/pipeline.py
          └─► run_rebalancing(request)
                ├─ step1_cap_and_spill
                ├─ step2_compare_and_decide
                ├─ step3_tax_classification
                ├─ step4_initial_trades_under_stcg_cap
                ├─ step5_loss_offset_top_up
                └─ step6_presentation
```

## 4. File map

### Files to add

| Path | Purpose |
|---|---|
| `app/services/ai_bridge/rebalancing/__init__.py` | Package marker. Mirrors `asset_allocation/__init__.py`; no auto-import of `chat.py` (cycle avoidance). |
| `app/services/ai_bridge/rebalancing/chat.py` | `@register("rebalancing")` handler; entry from chat dispatcher. |
| `app/services/ai_bridge/rebalancing/service.py` | Orchestrator: cache-first allocation lookup → run-or-skip allocation → build input → run pipeline → persist → format. |
| `app/services/ai_bridge/rebalancing/input_builder.py` | Maps ORM holdings + allocation output + tax_profile + fund-rank CSV → `RebalancingComputeRequest`. |
| `app/services/ai_bridge/rebalancing/formatter.py` | Deterministic sectioned-markdown renderer. Mirrors `format_allocation_chat_brief`. |
| `app/services/ai_bridge/rebalancing/tests/test_chat.py` | Handler dispatch + TurnContext mapping. |
| `app/services/ai_bridge/rebalancing/tests/test_input_builder.py` | Holdings + allocation + CSV → request mapping; tax-aging splits; default tax_profile fallback. |
| `app/services/ai_bridge/rebalancing/tests/test_service.py` | Cache-hit, cache-miss, stale-cache, blocking-message propagation, persistence, exception handling. |
| `app/services/ai_bridge/rebalancing/tests/test_formatter.py` | Snapshot tests for sectioned markdown (with and without warnings). |
| `app/services/ai_bridge/rebalancing/tests/test_persist.py` | Verifies `RebalancingRecommendation` row written with correct discriminator and FK. |
| `app/services/rebalancing_recommendation_persist.py` | Persist helper for `RebalancingComputeResponse` rows. Mirrors `allocation_recommendation_persist.py`. |
| `app/routers/ai_modules/rebalancing.py` | Optional debug endpoint `POST /api/v1/ai-modules/rebalancing/compute`. |
| `alembic/versions/<timestamp>_extend_tax_profile_and_rebalancing.py` | Migration for tax_profile fields + recommendation_type + source_allocation_id. |

### Files to modify

| Path | Change |
|---|---|
| `AI_Agents/src/intent_classifier/models.py` | Add `REBALANCING = "rebalancing"` to the intent enum. |
| `AI_Agents/src/intent_classifier/prompts.py` | One-line description + 2-3 examples distinguishing `REBALANCING` from `PORTFOLIO_OPTIMISATION`. |
| `app/services/chat_core/brain.py` | Add a branch around line 121 that lazy-imports `rebalancing.chat` and dispatches when intent is `REBALANCING` (mirrors the allocation branch). |
| `app/services/ai_bridge/__init__.py` | Re-export new bridge entry points if consistent with existing pattern. |
| `app/routers/__init__.py` | Mount the new `ai_modules/rebalancing.py` router. |
| `app/models/profile/tax_profile.py` | Add `tax_regime`, `carryforward_st_loss_inr`, `carryforward_lt_loss_inr` columns. |
| `app/models/rebalancing.py` | Add `recommendation_type` enum column + optional `source_allocation_id` self-FK. |

### Files NOT touched

- `AI_Agents/src/Rebalancing/` — engine code is read-only for this work.
- `AI_Agents/src/goal_based_allocation_pydantic/` — used as-is.
- `app/services/ai_bridge/asset_allocation/` — reused as-is via `compute_allocation_result`.
- `app/services/ai_bridge/ailax_flow.py` — its `SpineMode.REBALANCE` regex becomes redundant
  once the classifier handles routing; deprecate in a follow-up, not in this work.

## 5. Section detail

### 5.1 Intent classification & dispatch

**Classifier change** — `AI_Agents/src/intent_classifier/models.py:11-16`. Append:

```python
REBALANCING = "rebalancing"
```

**Prompt update** — wherever the classifier's `ChatPromptTemplate` lives. Add a short description:
- `PORTFOLIO_OPTIMISATION`: "what should my asset allocation be?", "how should I allocate ₹50L across goals?"
- `REBALANCING`: "rebalance my portfolio", "what trades should I make to align with my plan?", "show me what to buy and sell"

The boundary is *"where should I be"* → optimisation vs *"how do I get there"* → rebalancing.

**Handler registration** — new `app/services/ai_bridge/rebalancing/chat.py`. Mirrors the
allocation pattern at `asset_allocation/chat.py:170-192`:

```python
from app.services.ai_bridge.chat_dispatcher import register
from app.services.ai_bridge.rebalancing.service import compute_rebalancing_result

@register("rebalancing")
async def handle(ctx: TurnContext) -> ChatHandlerResult:
    outcome = await compute_rebalancing_result(
        user=ctx.user,
        user_question=ctx.user_message,
        db=ctx.db,
        acting_user_id=ctx.acting_user_id,
        chat_session_id=ctx.session_id,
    )
    if outcome.blocking_message:
        return ChatHandlerResult(
            text=outcome.blocking_message,
            snapshot_id=None,
            rebalancing_recommendation_id=None,
        )
    return ChatHandlerResult(
        text=outcome.formatted_text,
        snapshot_id=outcome.allocation_snapshot_id,
        rebalancing_recommendation_id=outcome.recommendation_id,
    )
```

**Brain wiring** — `app/services/chat_core/brain.py` ~line 121-133, parallel to the
allocation branch:

```python
elif intent_value == "rebalancing":
    import app.services.ai_bridge.rebalancing.chat  # noqa: F401  — @register side-effect
    result = await dispatch_chat("rebalancing", turn_context)
    # same telemetry / ChatBrainResult assembly as the allocation branch
```

A test under `app/services/ai_bridge/rebalancing/tests/test_chat.py` locks the
`@register` import side-effect (mirrors `test: lock @register import side-effect for
asset_allocation_chat`, commit `f4e312b`).

### 5.2 Service & control flow

`app/services/ai_bridge/rebalancing/service.py`. Mirrors `asset_allocation/service.py`.

```python
@dataclass(frozen=True)
class RebalancingRunOutcome:
    response: RebalancingComputeResponse | None
    formatted_text: str | None = None
    blocking_message: str | None = None
    recommendation_id: uuid.UUID | None = None        # the new rebalancing-trades row
    allocation_snapshot_id: uuid.UUID | None = None   # populated only when allocation re-ran
    used_cached_allocation: bool = False              # for telemetry
```

Entry: `compute_rebalancing_result(user, user_question, *, db, acting_user_id, chat_session_id) -> RebalancingRunOutcome`.

Control flow:

1. **Pre-flight blockers.**
   - Missing `user.date_of_birth` → reuse allocation's friendly DOB-needed message.
   - No mutual-fund holdings → "Connect your mutual fund portfolio and ask me again."
   - Missing `tax_profile` is **not** a blocker — defaults handle it.

2. **Cache lookup.** Query `RebalancingRecommendation` where `portfolio_id = primary_portfolio.id`,
   `recommendation_type = ALLOCATION`, ordered by `created_at DESC`, limit 1. If found and
   `created_at >= now() - 90 days`, parse `recommendation_data["goal_allocation_output"]` back
   into `GoalAllocationOutput`.

3. **Cache miss / stale.** Call `compute_allocation_result(user, user_question, db=db,
   persist_recommendation=True, acting_user_id=acting_user_id, chat_session_id=chat_session_id,
   spine_mode="rebalance_chained")`. If it returns a `blocking_message`, propagate as the
   rebalancing blocker. Otherwise capture `result` (`GoalAllocationOutput`) and `allocation_snapshot_id`.

4. **Build the engine request.** `await build_rebalancing_input_for_user(user, allocation_output, db)`.

5. **Run the engine.** `response = await asyncio.to_thread(run_rebalancing, request)`. Pure-sync
   CPU work; thread offload keeps the event loop free. No env-var dance — no LLM in the rebalancing path.

6. **Persist.** `persist_rebalancing_recommendation(db, acting_user_id, response, *,
   chat_session_id, source_allocation_id, used_cached_allocation)` → returns `recommendation_id`.

7. **Telemetry.** `record_ai_module_run(...)` with `module="rebalancing"`, full input/output
   payload, correlation IDs. Same pattern as `asset_allocation/service.py:326-347`.

8. **Format.** `formatted_text = format_rebalancing_chat_brief(response,
   used_cached_allocation=cache_hit)`. On cache miss/stale, the formatter prepends a soft
   lead line: *"Worked out your asset allocation based on your goals first, then built a
   rebalancing plan from there."*

9. **Return.**

Error / blocker matrix:

| Condition | Behaviour |
|---|---|
| Missing DOB | Block early. |
| No mutual-fund holdings | Block early. |
| Allocation pipeline blocks (e.g. no API key) | Propagate allocation's message. |
| Recommended ISIN missing from `MfFundMetadata` (can't price) | Block: "we couldn't price one of the recommended funds". |
| `run_rebalancing` raises | Log; generic "I couldn't compute your rebalancing plan right now". |
| Engine warnings (`BAD_FUND_DETECTED`, `UNREBALANCED_REMAINDER`, `STCG_BUDGET_BINDING`, `NO_HOLDINGS_FOR_RECOMMENDED_FUND`) | Render in the **Things to note** section — not blockers. |

### 5.3 Input builder

`app/services/ai_bridge/rebalancing/input_builder.py`.

```python
async def build_rebalancing_input_for_user(
    user: User,
    allocation_output: GoalAllocationOutput,
    db: AsyncSession,
) -> tuple[RebalancingComputeRequest, dict[str, Any]]:  # (request, debug)
```

**Three data sources:**

1. **Sub-asset-group targets — from allocation output.**
   `target_by_subgroup = {r.subgroup: r.total for r in allocation_output.aggregated_subgroups}`.
   Used as the rank-1 `target_amount_pre_cap` for each subgroup. Fund-level distribution is
   the engine's job — we do not consume `fund_mappings`.
2. **Fund-rank table — `AI_Agents/Reference_docs/Prozpr_fund_ranking.csv`.**
   Loaded once at module import; cached as
   `dict[asset_subgroup, list[(rank, isin, sub_category, fund_name)]]` sorted by rank.
3. **Holdings, tax info, NAV, fund metadata — from DB.**

**Materialisation:**

1. **Net holdings ledger from `MfTransaction`** — per-ISIN list of remaining lots (FIFO),
   each with `acquisition_date`, `units`, `acquisition_nav`. Drop ISINs with zero net units.
   `total_corpus = Σ (lot.units × current_nav)` over all held ISINs. We do not rescale
   targets — engine's `UNREBALANCED_REMAINDER` warning surfaces any gap.

2. **Recommended-fund rows from the fund-rank table.** For each
   `(asset_subgroup, rank, isin, sub_category, fund_name)`:
   - `target_amount_pre_cap = target_by_subgroup[asset_subgroup]` if `rank == 1`, else `0`.
   - If ISIN held → enrich with `present_allocation_inr`, `invested_cost_inr`, ST/LT split,
     `units_within_exit_load_period`, `current_nav`, `fund_rating`.
   - If not held → all present-side fields are 0; `current_nav` from `MfNavHistory`;
     `fund_rating` from metadata (default 10).
   - `is_recommended = True`.

3. **BAD-fund rows.** For every held ISIN not in the recommended set: build a row with
   `rank=0`, `target_amount_pre_cap=0`, `is_recommended=False`. asset_subgroup, sub_category,
   fund_name from `MfFundMetadata`. Same per-lot enrichment as above.

**Tax-aging per lot.** Classify each lot as ST or LT using the same env-var defaults the
engine reads (`REBAL_ST_THRESHOLD_EQUITY=12` months, `REBAL_ST_THRESHOLD_DEBT=24` months) —
single source of truth shared between builder and engine via a small helper in
`AI_Agents/src/Rebalancing/config.py`. Asset class comes from `MfFundMetadata`. Sum:

- `st_value_inr = Σ ST lots × current_nav`
- `st_cost_inr  = Σ ST lots × acquisition_nav`
- `lt_value_inr / lt_cost_inr` — same for LT.
- `units_within_exit_load_period = Σ lot.units` for lots within `exit_load_months` of today.

**Request-level tax inputs from `TaxProfile`:**

- `tax_regime` — new column, default `"new"`.
- `effective_tax_rate_pct` — `income_tax_rate`, default `30.0`.
- `carryforward_st_loss_inr` / `carryforward_lt_loss_inr` — new columns, default `0`.
- `stcg_offset_budget_inr = None` (advanced; v1 omits).
- `rounding_step = 100`.

**Edge cases the builder handles:**

| Case | Handling |
|---|---|
| Held ISIN missing from `MfFundMetadata` | Treat as BAD with rating=10, exit-load=0; debug warning. |
| Held ISIN missing from `MfNavHistory` | Use latest transaction's NAV as fallback; debug warning. |
| Recommended ISIN not yet held | Zero present-side fields; pull `current_nav` from `MfNavHistory`. |
| Recommended ISIN missing from `MfFundMetadata` | Hard error → blocking message; do not run engine. |
| Recommended ISIN missing from `MfNavHistory` (no NAV available, not held) | Hard error → blocking message; do not run engine. |
| Empty `MfTransaction` ledger | Defensive raise (caller should have already blocked). |

Returned debug dict captures: corpus, count of lots per ISIN, count of BAD funds detected,
fallbacks used. Logged via `trace_line`.

### 5.4 Persistence & schema changes

One Alembic migration covers all three changes.

**1. `app/models/rebalancing.py` — add a discriminator:**

```python
class RecommendationType(str, Enum):
    ALLOCATION = "allocation"                  # goal-based allocation output
    REBALANCING_TRADES = "rebalancing_trades"  # trade-list output
```

Add `recommendation_type: Mapped[RecommendationType]` (NOT NULL, indexed). Backfill existing
rows to `ALLOCATION` in the migration. Add `source_allocation_id: Optional[UUID]` self-FK so
a `REBALANCING_TRADES` row points back to the `ALLOCATION` row it consumed (audit + cache).

**2. `app/models/profile/tax_profile.py` — add three columns:**

- `tax_regime: Mapped[Optional[str]]` (nullable; values `"old"|"new"`).
- `carryforward_st_loss_inr: Mapped[Decimal]` (default `0`).
- `carryforward_lt_loss_inr: Mapped[Decimal]` (default `0`).

All default safely; no data backfill needed beyond column defaults.

**3. New persistence helper — `app/services/rebalancing_recommendation_persist.py`:**

Mirrors `allocation_recommendation_persist.py` 1:1. Writes one `RebalancingRecommendation` row:

- `recommendation_type = REBALANCING_TRADES`
- `recommendation_data = {"rebalancing_response": response.model_dump(mode="json"),
   "request_id": request_id, "used_cached_allocation": bool}`
- `source_allocation_id = <id of the allocation row used>`
- `status = pending`
- `chat_session_id`, `user_question` (traceability)

The cache lookup in `service.py` reads `WHERE recommendation_type = ALLOCATION ORDER BY
created_at DESC LIMIT 1`.

### 5.5 Output formatter

`app/services/ai_bridge/rebalancing/formatter.py`. Pure Python. Sectioned markdown.

**Voice:** financially-savvy friend, not advisor. Plain language, no boilerplate
disclaimers, conversational connectors, contractions OK. Numbers stay precise — we're
direct, just not clinical. No "we recommend", no "as per your risk profile", no "kindly".
All copy lives in template strings in `formatter.py` so we can iterate on tone without
touching the structured data path.

**Style rules baked into the templates:**

- Lead with what we did, not what the user should do.
- Headers describe outcomes ("you'd land at ₹X") rather than categorise ("Asset mix").
- Reasons read as "because …" or trail in parentheses, not as separate "rationale" labels.
- Tax + cost line is single-sentence, not a labelled grid.
- Warnings get one short heads-up sentence each, not bullet codes.
- Closing is one casual sanity-check, not a compliance disclaimer.

**Template (illustrative, not literal — copy lives in `formatter.py`):**

```
[Optional lead line — only when allocation was refreshed in this turn]
First I redid your asset mix from your goals, then worked out the trades to get there.

[Opener]
Here's how I'd rebalance — <N> moves on a corpus of about ₹<total_corpus>.

[Per-subgroup block, one per SubgroupSummary]
**Large Cap** — you'd land at ₹<suggested_final> (target was ₹<goal_target>).
- Put ₹<amt> into <fund> — <reason_text>.
- Pull ₹<amt> out of <fund> — <reason_text>.
- Leaving ₹<amt> in <fund> as-is.

[Tax + cost line — only if non-zero]
The trade-offs: about ₹<tax_estimate> in taxes and ₹<exit_load> in exit loads, with
₹<stcg_realised> short-term and ₹<ltcg_realised> long-term gains realised.

[Heads-up section — only if warnings present]
**A couple of heads-ups:**
- <fund_name> isn't on the recommended list anymore — worth exiting when the tax math works.
- ₹<remainder> couldn't be placed cleanly under the per-fund caps — small enough to ignore.

[Closing]
_Worth a sanity check on exit loads and tax before you pull the trigger._
```

**Per-warning copy.** Each `WarningCode` maps to a one-sentence template:

| Warning | Template |
|---|---|
| `BAD_FUND_DETECTED` | "<fund_names> aren't on the recommended list anymore — worth exiting when the tax math works." |
| `UNREBALANCED_REMAINDER` | "₹<amount> couldn't be placed cleanly under the per-fund caps — small enough to ignore." |
| `STCG_BUDGET_BINDING` | "Held back some sells to keep short-term gains under your offset budget." |
| `NO_HOLDINGS_FOR_RECOMMENDED_FUND` | "<fund_name> is on your plan but you don't hold it yet — fresh purchase." |

`test_formatter.py` snapshots cover each warning combination and the tax-zero / tax-non-zero
branches so copy regressions are caught.

### 5.6 HTTP debug endpoint

`app/routers/ai_modules/rebalancing.py`. `POST /api/v1/ai-modules/rebalancing/compute`.
Mirror of `ai_modules/asset_allocation.py`. Auth via `get_effective_user`, calls
`compute_rebalancing_result(...)`, returns
`{answer_markdown, recommendation_id, allocation_snapshot_id, used_cached_allocation}`.
For direct testing without going through chat.

### 5.7 Tests

| Test file | Coverage |
|---|---|
| `tests/test_chat.py` | `@register("rebalancing")` is bound; handler unpacks `TurnContext`; outcome → `ChatHandlerResult` mapping. |
| `tests/test_input_builder.py` | (a) Held-only, recommended-only, BAD-only, mixed cases yield correct row sets. (b) Tax-aging splits correctly given lots straddling the 12-month threshold. (c) Missing tax_profile uses defaults. (d) Sub-asset-group target picked up at rank-1 only. |
| `tests/test_service.py` | (a) Cache hit (≤90d) → no allocation run. (b) Cache miss → allocation runs, lead line shows. (c) Cache stale (>90d) → allocation re-runs. (d) Allocation blocking message propagates. (e) Persistence writes `REBALANCING_TRADES` with `source_allocation_id`. (f) `run_rebalancing` exception → friendly error. |
| `tests/test_formatter.py` | Snapshot of sectioned markdown for canonical responses (with and without warnings). |
| `tests/test_persist.py` | `RebalancingRecommendation` row written with correct discriminator, JSON shape, FK. |
| `app/services/chat_core/tests/test_rebalancing_e2e.py` | End-to-end: a "rebalance my portfolio" message with a fixture user produces the expected sections. |

## 6. Out of scope (v1)

- LLM-narrated chat reply on top of the formatter (deterministic markdown is shipped first).
- `stcg_offset_budget_inr` collection (left at `None`; advanced feature for later).
- UI for editing tax profile fields (`tax_regime`, carryforwards) — they default sensibly.
- Deprecation of `ailax_flow.py:SpineMode.REBALANCE` regex — handled in a follow-up.
- Approval / execution of trade lists (the existing `RebalancingRecommendation.status`
  workflow is reused, but no UX changes here).
- Performance / caching of fund-rank CSV beyond a single in-process module-level dict.

## 7. Open items deferred to the implementation plan

- Exact location of the classifier prompt (`AI_Agents/src/intent_classifier/prompts.py` is
  the assumed path — to be confirmed in implementation).
- Whether `MfFundMetadata` already carries the asset-class needed for ST/LT threshold lookup,
  or whether a join with another table is required.
- Whether `allocation_recommendation_persist.py` needs a small refactor to also stamp
  `recommendation_type = ALLOCATION` on its writes (likely yes; small touch).

## 8. References

- Engine entry: `AI_Agents/src/Rebalancing/pipeline.py:20`
- Engine schemas: `AI_Agents/src/Rebalancing/models.py:23-221`
- Engine module CLAUDE.md: `AI_Agents/src/Rebalancing/CLAUDE.md`
- Dev precursor (template the production builder mirrors):
  `AI_Agents/src/Rebalancing/Testing/Master_testing/bridge.py`
- Asset-allocation precedent (closest analogue for everything below the bridge):
  `app/services/ai_bridge/asset_allocation/`
- Existing rebalancing CRUD (router): `app/routers/rebalancing.py`
- Allocation persistence template:
  `app/services/allocation_recommendation_persist.py`
- Fund-rank CSV: `AI_Agents/Reference_docs/Prozpr_fund_ranking.csv`
