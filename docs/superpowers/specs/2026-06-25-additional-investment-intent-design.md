# Additional-investment intent — design

- **Date:** 2026-06-25
- **Status:** Draft (awaiting review)
- **Scope:** Prozpr_Backend only (new agent module + new chat intent). No frontend changes required for v1; Invest-page surfacing optional (see §9).

## Problem

A customer with money to deploy — *"I have ₹5L to invest, which funds?"* or *"I want a
₹50k/month SIP, which funds?"* or *"which large-cap fund should I invest in?"* — cannot
get a complete, one-shot answer today. The "where" and the "which funds" live in two
different intents and neither names funds the way the customer asked:

- A "where to invest" question routes to **asset_allocation**, which deliberately stops at
  the asset-class mix and **never names funds** (`prompts.py:17`, `33-36`).
- A "which fund" question routes to **rebalancing**, whose engine is built for
  drift-correction with tax-aware sells — it answers fund-picks only as a side effect of
  owning the fund-ranking list, and for fresh money it inflates corpus and re-runs the full
  allocation (emergency bucket included), which is not how new money should be allocated.
- A **SIP** is silently treated as a one-time lumpsum; there is no recurring/cadence concept
  anywhere in the allocation or rebalancing path.

So the customer must ask twice, the SIP framing is dropped, and fund names never reach the
chat reply (they only ever appear on the rebalancing Invest page).

## Current routing (the weak seam)

| Intent | Job today | Issue for this feature |
|---|---|---|
| `asset_allocation` | target **mix** (asset-class / subgroup) | refuses to name funds by design |
| `rebalancing` | fix drifted holdings (buy **and** sell, tax-aware) **+** "which fund?" | fund-selection is bolted on; fresh-money path re-runs full allocation incl. emergency |
| `goal_planning` | feasibility math (will I hit my goal?) | correct as-is; keep SIP-feasibility here |

Fund-selection is conflated into `rebalancing` only because rebalancing owns the
fund-ranking machinery. Conceptually "deploy *new* money into funds" is neither a mix
decision nor a drift correction.

## Goal

A new **`additional_investment`** intent + a dedicated, deterministic engine that answers
"deploy this money — which funds?" in one shot, for both **lumpsum and SIP**, **naming the
funds in the chat reply**. It is **BUY-only** (deploys new money; never sells existing
holdings — that stays in `rebalancing`) and **holdings-aware** (tops up acceptable existing
funds before buying new ones). It reuses the practical-allocation output, the goal-funding
status, and the fund-ranking list rather than inventing any of them.

The conceptual model becomes a clean three-way split:

| Intent | One job |
|---|---|
| `asset_allocation` | the target **mix** |
| **`additional_investment`** (new) | deploy new money + pick funds — **BUY-only, holdings-aware** |
| `rebalancing` | move existing money to fix drift — buy *and* sell, tax-aware |

## Locked decisions

1. **New flat intent now; LangGraph orchestrator is Phase 2.** Engine steps are identical
   either way; the classifier entry added now becomes the graph's entry later. The graph,
   when built, **wraps** the unchanged engines (allocation, additional_investment,
   rebalancing) — it does not absorb them. Trigger for Phase 2: real demand for compound
   ("deploy this *and* rebalance the rest") or iterative (propose→react→adjust) MF turns.
2. **Fund-to-fund swaps stay in `rebalancing`.** The new intent is for *new* money and
   *fresh* selection only. This boundary is written explicitly into the classifier prompt.
3. **Fresh-money allocation rule (the core logic):** from the practical allocation's
   per-bucket subgroup amounts and per-bucket goal-funding status —
   - **Emergency is always excluded** — fresh investment money never tops up emergency.
   - **If medium-term goals are fulfilled OR there are no medium-term goals →** use the
     **long-term** bucket's subgroup mix: `ratio[sg] = long_term[sg] / Σ long_term`.
     (By the waterfall, medium-fulfilled implies short + emergency are covered. If long-term
     is *also* fulfilled, still use the long-term mix — surplus invests for growth.)
   - **Else →** use **total − emergency**, renormalised:
     `ratio[sg] = (total[sg] − emergency[sg]) / Σ(total[sg] − emergency[sg])`.
   - `target_inr[sg] = ratio[sg] × deploy_amount`.
4. **"Medium-term goals fulfilled" = every medium-horizon goal `is_funded`.** Horizon is not
   re-defined: reuse the allocation engine's existing goal→bucket grouping (months thresholds
   `MEDIUM_TERM_BOUNDARY_MONTHS=36`, `LONG_TERM_BOUNDARY_MONTHS=72`,
   `asset_allocation_pydantic/tables.py:164-165`) so "medium-term goals" matches the buckets
   whose ratios we read.
5. **Caps = reuse the rebalancing cap table** (`Rebalancing/tables.py` + `config.py`):
   multi_asset 20%, short_debt/arbitrage 30%, default/equity 10%, on the **resulting total
   corpus** (existing holdings + deployed amount). Overflow spills rank-1 → rank-2 → rank-3.
6. **Fund-selection kernel: contain now, share in Phase 2.** v1 reuses the already-clean
   shared pieces (the `fund_rank.get_fund_ranking()` loader and the cap tables) and writes a
   small BUY-only allocator inside the new engine. It does **not** refactor rebalancing's
   internal step1 logic. A shared pure kernel is deferred to Phase 2 (alongside the graph),
   keeping the working rebalancing engine untouched for v1. *(Consistency call: matches the
   "ship small, converge later" sequencing chosen for routing.)*
7. **Rounding:** per-fund amounts in multiples of ₹100 (matches the engine's existing
   `all_amounts_in_multiples_of_100` convention).
8. **Cadence does not change the ratio.** SIP = same per-subgroup ratio applied to the
   monthly amount; lumpsum = applied to the whole. Only framing differs in the reply.
9. **Naming defaults (recommended, mirror existing modules):** intent value
   `additional_investment`; src folder `AI_Agents/src/additional_investment/` (snake_case);
   engine subfolder `ainv_engine` (`ai_engine` is taken by the chat-brain domain).
10. **Use the customer's *current* practical allocation shape; do NOT re-run it with the
    deploy amount added.** We read the existing bucket ratios and apply them to the new money.
    This is the deliberate departure from today's `additional_cash_inr` path, which inflates
    the corpus and re-runs the full allocation (emergency bucket included) — the exact
    behaviour the Problem section calls out.

## Design

### 1. Data sources (all confirmed to exist on the current branch)

- **Per-bucket subgroup allocation** — `run_practical_allocation(...)` →
  `PracticalAllocationOutput.aggregated_subgroups: List[AggregatedSubgroupRow]`; each row has
  `subgroup, emergency, short_term, medium_term, long_term, total` as floats
  (`AggregatedSubgroupRow` def `asset_allocation_pydantic/models.py:109-115`). Branch 1 reads
  `row.long_term`; branch 2 reads `row.total − row.emergency` per row. **The allocation is
  computed on the customer's *current* corpus — the deploy amount is NOT added in** (see
  decision #10); we read the bucket *shape* and normalise to a ratio, so absolute corpus size
  is irrelevant and the deploy amount may exceed any bucket without issue.
- **Goal funding** — `cashflow_statement` `GoalFundingStatus.is_funded` + `goal_date`
  (`cashflow_statement/models.py:279-293`); list at `GoalPlanningOutput.goals`; overall
  `headline.is_feasible`.
- **Fund ranking** — `AI_Agents/Reference_docs/prozpr_fund_ranking_may_2026.csv`, loaded via
  `app/domains/rebalancing/services/rebal_engine/fund_rank.py` `get_fund_ranking()`. Rank-1 is
  deterministic per `(asset_subgroup, sub_category)`.
- **Current holdings** — same per-holding source rebalancing uses
  (`rebal_engine/holdings_ledger.py`), for the holdings-aware top-up.

### 2. Agent engine — `AI_Agents/src/additional_investment/`

Pure-Python, no LLM (mirrors `practical_asset_allocation`). Files: `__init__.py`,
`pipeline.py` (entry), `models.py`, `config.py` (optional), `utils.py`, `Testing/`.

- **Entry:** `run_additional_investment(inp: AdditionalInvestmentInput) -> AdditionalInvestmentOutput`.
  *(Input/Output naming follows the allocation family; `…ComputeRequest/Response` is the
  rebalancing alternative — see open item O5.)*
- **Input** carries: `deploy_amount_inr`, `cadence` (`lumpsum` | `sip_monthly`),
  `aggregated_subgroups` (from practical alloc), per-bucket `funded` flags (derived from goal
  funding), the ranked fund list, current holdings per subgroup, and the resulting-corpus
  figure for caps.
- **Logic:**
  1. Determine `medium_fulfilled` (decision #4); pick branch (decision #3) → per-subgroup ratio.
  2. `target_inr[sg] = ratio[sg] × deploy_amount`.
  3. **Fund selection (BUY-only, holdings-aware):** for each subgroup with a target, prefer
     topping up an *acceptable existing holding* in that subgroup (held, rank present /
     rating ≥ 5, not a force-exit fund); otherwise buy the rank-1 fund. Apply the per-fund
     cap on resulting corpus; spill overflow rank-1 → rank-2 → rank-3. Round to ₹100.
  4. Emit a **BUY list** + which branch was used (for transparency in the reply).
- **Output:** `buys: List[FundBuy]` (`recommended_fund`, `isin`, `sub_category`,
  `asset_subgroup`, `amount_inr`, `monthly_amount_inr` when SIP, `reason`), the per-subgroup
  target table, and the `branch_used` enum.

### 3. Cadence

The handler parses the amount and cadence from the question (parallel to how the AA handler
already parses `additional_cash_inr`): a lumpsum amount, or a SIP monthly amount. The ratio
is computed from the customer's **practical allocation** (their overall picture), then applied
to the deploy amount. For SIP, `monthly_amount_inr[sg] = ratio[sg] × sip_monthly`. Reply
framing differs only ("Invest ₹X in Fund A" vs "Set up ₹X/month in Fund A").

### 4. App domain — `app/domains/additional_investment/`

Mirrors `rebalancing/`:
```
additional_investment/
├── __init__.py
├── CLAUDE.md
├── models/
│   ├── __init__.py
│   └── additional_investment_run.py        # ORM AdditionalInvestmentRun
├── schemas/
│   ├── __init__.py
│   └── run.py                              # AdditionalInvestmentRunDetailResponse + child Schemas
├── routers/
│   └── __init__.py
└── services/
    ├── __init__.py
    ├── additional_investment_module_service.py   # public async run(turn, ctx, prior)
    ├── additional_investment_persist_service.py
    └── ainv_engine/
        ├── __init__.py
        ├── service.py            # builds engine input, calls run_additional_investment
        ├── input_builder.py      # assembles practical-alloc + funding + ranking + holdings
        ├── chat.py               # @register("additional_investment") + _AINV_FORMATTER_BODY
        └── tests/
```

### 5. Chat handler + dispatch

- `ainv_engine/chat.py`: `@register("additional_investment") async def handle(ctx) -> ChatHandlerResult`.
- Registry mechanism unchanged: `register` / `dispatch_chat` / `_HANDLERS` in
  `app/domains/ai_engine/chat_dispatcher.py`. Trigger registration via a lazy import in
  `additional_investment_module_service.py`:
  `from ...ainv_engine import chat as _ainv_chat  # noqa: F401`.

### 6. Flow wiring — `app/domains/ai_engine/services/flow.py`

```python
async def flow_additional_investment(turn, ctx) -> ModuleOutput:
    from app.domains.practical_asset_allocation.services.practical_asset_allocation_module_service import run as run_paa
    from app.domains.additional_investment.services.additional_investment_module_service import run as run_ainv
    paa = await run_paa(turn, ctx, {})
    return await run_ainv(turn, ctx, {AIModule.ASSET_ALLOCATION.value: paa})
```
Add one row to `FLOWS`: `"additional_investment": flow_additional_investment`. Goal-funding
status is obtained inside `run_ainv` (read persisted goal-planning result if present, else
compute) — see open item O2.

### 7. Classifier changes — `AI_Agents/src/intent_classifier/`

- `models.py`: add `ADDITIONAL_INVESTMENT = "additional_investment"` to `Intent` (currently
  `models.py:7-14`).
- `classifier.py`: add `"additional_investment"` to `_IntentLiteral` (`classifier.py:29-37`),
  same order. The drift test `app/domains/ai_engine/tests/test_intent_classifier_schema.py`
  enforces enum↔Literal parity.
- `prompts.py` — add an `### additional_investment` intent definition and **re-adjudicate
  three boundaries** (this is the delicate part):
  - **Move from rebalancing →** "which large-cap fund should I invest in?", "which mutual
    fund is best for me?" (fresh selection, no existing-money move) — currently rebalancing
    examples at `prompts.py:147,162-163`.
  - **Move from asset_allocation →** "I have ₹5L, which funds?", "SIP of ₹50k — which funds?"
    when the ask is fund deployment. Pure mix questions ("equity vs debt for me?") stay in AA.
  - **Keep in rebalancing →** fund-to-fund swaps ("switch from Axis to Mirae") and
    over/under-weight diagnostics. **Keep in goal_planning →** SIP feasibility ("at ₹50k/mo
    will I hit ₹2cr?").
  - Define the discriminator crisply: *additional_investment = deploy a specified/new amount
    and/or select funds, BUY-only*; *rebalancing = move existing money*; *asset_allocation =
    the target mix itself*.
- Follow-up transitions: define that after an `asset_allocation` mix answer, "ok, which funds
  for my ₹X?" transitions to `additional_investment` (parallel to the existing AA→rebalancing
  accept transition).

### 8. Formatter

`_AINV_FORMATTER_BODY` constant in `ainv_engine/chat.py` (mirrors `_REBAL_FORMATTER_BODY`,
`rebal_engine/chat.py:160-218`), passed as `body_prompt` to the shared
`format_with_telemetry`. FACTS_PACK shape: `deploy_amount_inr/_indian`, `cadence`,
`branch_used`, `buys` (fund name, sub_category, amount, monthly amount), `per_subgroup_target`.
The reply **names the funds** (the core requirement).

### 9. Persistence + Invest page (optional for v1)

Mirror rebalancing: `AdditionalInvestmentRun` ORM (registered in
`additional_investment/models/__init__.py` **and** `app/all_models.py`), a persist service, and
`AdditionalInvestmentRunDetailResponse` exposing the BUY list for a richer Invest-page view.
The chat reply already names funds, so persistence/Invest-page can ship in a fast-follow if we
want a chat-only v1 (open item O4).

## Out of scope (flag, do not build here)

- The **LangGraph MF orchestrator** and the **shared fund-selection kernel** (both Phase 2).
- Any change to the **rebalancing** engine internals (decision #6).
- **Time-phased SIP projection** (corpus growth month-over-month, STP) — explicitly rejected;
  v1 is a static monthly split of the ratio.
- Sourcing fund composition / new ranking data — uses the existing CSV as-is.

## Open items to resolve in implementation (defaults chosen)

- **O1 — Cap base for SIP.** Default: apply caps as for lumpsum, against resulting corpus.
  A monthly SIP rarely hits per-fund caps; revisit only if it distorts small SIPs.
- **O2 — Goal-funding retrieval in the flow.** Default: read the persisted goal-planning
  result for the customer; compute via the cashflow engine only if absent. Confirm a persisted
  source exists and is fresh enough.
- **O3 — "Acceptable existing holding" rule for top-up.** Default: held in that subgroup AND
  (rank present OR rating ≥ 5) AND not a force-exit (rank 9999) fund. Otherwise buy rank-1.
- **O4 — v1 surface:** chat-only (names funds in reply) vs chat + persisted Invest page.
  Default: include persistence to match conventions; can defer if we want a thinner v1.
- **O5 — I/O model naming:** `AdditionalInvestmentInput/Output` (allocation family) vs
  `…ComputeRequest/Response` (rebalancing). Default: `Input/Output`.
- **O6 — Emergency underfunded:** rule still excludes emergency from fresh money (decision #3).
  Open product choice: whether the reply should *nudge* ("your emergency fund isn't full")
  rather than silently skip it. Default: a one-line nudge in the formatter body, no logic change.

## Risks / to verify

1. **Classifier boundary re-adjudication (§7) is the highest-risk change.** Moving
   fund-selection examples between three intents will cause misroutes until the prompt + eval
   set are tuned. Needs dedicated eval coverage before ship (see Acceptance).
2. **Branch determination correctness.** "medium fulfilled" must group goals by the *same*
   horizon definition the buckets use (decision #4), or the branch and the ratios disagree.
3. **Holdings-aware top-up vs caps interaction.** Topping up an existing fund then applying a
   per-fund cap on resulting corpus must not double-count; verify with a held-fund fixture.
4. **No-data customers.** New investor (zero holdings) → all rank-1 BUYs, no top-up; customer
   with no goals → long-term mix (waterfall vacuously fulfilled). Both must produce a valid answer.

## Acceptance criteria

- A customer message "I have ₹5L, which funds?" routes to `additional_investment` and returns
  a chat reply that **names specific funds with ₹ amounts**, summing to ₹5L (±rounding), with
  **no emergency-bucket allocation**.
- "SIP of ₹50k/month, which funds?" returns the **same funds** framed as monthly amounts
  (₹/month), summing to ₹50k.
- When the customer's medium-term goals are all `is_funded`, the mix matches the **long-term**
  bucket ratio; otherwise it matches **total − emergency** renormalised — verified to the rupee
  on fixtures for both branches.
- Fund-to-fund swaps and "should I rebalance?" still route to `rebalancing`; SIP-feasibility
  still routes to `goal_planning` (eval set asserts no regression).
- `test_intent_classifier_schema.py` passes with the new intent; new engine unit tests cover
  both branches, emergency exclusion, renormalisation, rank-1 mapping, caps/overflow, and
  holdings top-up.

## Key references

- Practical alloc output: `AI_Agents/src/practical_asset_allocation/pipeline.py`
  `run_practical_allocation` → `PracticalAllocationOutput.aggregated_subgroups`;
  `AggregatedSubgroupRow` `asset_allocation_pydantic/models.py:109-115`.
- Goal funding: `AI_Agents/src/cashflow_statement/models.py` `GoalFundingStatus` (279-293,
  `is_funded`, `goal_date`), `GoalPlanningOutput.goals` (461), `headline.is_feasible`.
- Horizon thresholds: `asset_allocation_pydantic/tables.py:164-165` (36 / 72 months).
- Fund ranking: `AI_Agents/Reference_docs/prozpr_fund_ranking_may_2026.csv`;
  `app/domains/rebalancing/services/rebal_engine/fund_rank.py` `get_fund_ranking()`.
- Caps: `AI_Agents/src/Rebalancing/tables.py` + `config.py` (multi_asset 20%, short_debt /
  arbitrage 30%, default 10%).
- Intent enum + Literal + drift test: `intent_classifier/models.py:7-14`,
  `intent_classifier/classifier.py:29-37`, `app/domains/ai_engine/tests/test_intent_classifier_schema.py`.
- Classifier prompt + boundaries: `AI_Agents/src/intent_classifier/prompts.py` (rebalancing
  fund examples 147,162-163; AA fresh-money example 22; SIP-feasibility goal_planning 57).
- Chat dispatch: `app/domains/ai_engine/chat_dispatcher.py` (`register`, `dispatch_chat`, `_HANDLERS`).
- Flow + module-service contract: `app/domains/ai_engine/services/flow.py` (`flow_rebalancing`
  runs PAA then rebalancing; `FLOWS` table); module `run(turn, ctx, prior) -> ModuleOutput`.
- Formatter: `app/domains/ai_engine/answer_formatter/formatter.py`; bodies
  `_REBAL_FORMATTER_BODY` (`rebal_engine/chat.py:160-218`), `_AA_FORMATTER_BODY`
  (`aa_engine/chat.py:197`).
- ORM registration: `app/all_models.py` + domain `models/__init__.py`.
- Holdings source: `app/domains/rebalancing/services/rebal_engine/holdings_ledger.py`.
