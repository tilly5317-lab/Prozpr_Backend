# AI_Agents/src/additional_investment — deploy fresh money (lumpsum/SIP) into specific funds

Pure-Python engine: given a deploy amount (lumpsum or monthly SIP) plus the customer's per-bucket subgroup allocation and goal-funding status, it picks specific funds to BUY (never sells), recommending purely from the ranked fund list (holding-agnostic — current holdings are not consulted). Distinct from `asset_allocation_pydantic` (target mix only) and `Rebalancing` (moves existing money, buy+sell) — this only adds new money.

## Entry / contract
- Entry `run_additional_investment(inp: AdditionalInvestmentInput) → AdditionalInvestmentOutput`.
- Input (caller-populated; the engine imports no peer agent): deploy amount + cadence, per-subgroup bucket amounts (mirrors `asset_allocation_pydantic`'s `AggregatedSubgroupRow`), `short_term_fulfilled` + `medium_term_fulfilled` bools (long-term is the fallback target, so no `long_term_fulfilled` flag), the ranked fund list, the cap percentages, and `exclude_subgroups` (subgroups ineligible for fresh money).
- Output: `SubgroupTarget` table, the BUY list (`FundBuy`), `target_bucket` (which horizon was funded), and `deployed_inr`/`undeployed_inr`.

## Files
- `__init__.py` — public re-exports (entry + I/O models).
- `models.py` — pydantic I/O models.
- `ratio.py` — bucket selection + subgroup split (`select_target_bucket`, `compute_targets`).
- `selection.py` — BUY-only, holding-agnostic fund selection from the ranking (`select_funds`).
- `pipeline.py` — entry orchestrator + SIP cadence framing.
- `Testing/` — pytest suite (gitignored).

## Gotchas & invariants
- **Pure engine.** No LLM, no I/O, no cross-agent imports. Cap percentages are passed IN (caller sources them from `Rebalancing/tables.py`) — not hardcoded here, to keep one source of truth (`models.py`).
- **Bucket targeting (nearest unfunded goal).** `select_target_bucket` picks short → medium → long-term; long-term is the fallback whenever short+medium are fulfilled (so the all-funded case keeps building long-term). `compute_targets` weights subgroups by that bucket's column and renormalises. Emergency is never a target. A targeted bucket with no allocation ⇒ empty targets ⇒ fully undeployed (no fall-through) (`ratio.py`).
- **BUY-only + holding-agnostic.** Recommends purely from the ranked fund list (rank-1 first); overflow spills down the ranking; amounts rounded down to ₹100 (`selection.py`).
- **Per-fund cap keys off the DEPLOY amount, not corpus.** `cap_amt = pct × deploy_amount` (`selection.py`, `_cap_amount`) — same percentages as Rebalancing but a different base, since the corpus-based cap never binds on a small SIP. A subgroup's share thus spreads across its top funds; Rebalancing caps on corpus, this deliberately diverges.
- **`exclude_subgroups` get zero weight** in `ratio.py` — no target, share renormalises onto eligible subgroups. Caller policy: `non_mf_equities` (no funds) and `tax_efficient_equities` (ELSS lock-in).
- **Can under-deploy.** When caps bind and a subgroup lacks funds, `sum(buys) < deploy_amount`; the gap is surfaced as `undeployed_inr`, never silently dropped (`pipeline.py`).
- **Cadence does not change the ratio.** SIP applies the same per-subgroup ratio to the monthly amount; only the `monthly_amount_inr` framing differs (`pipeline.py`).
- **Follows the allocation family, not Rebalancing, for I/O shape.** Money is `float` (rupees), matching `practical_asset_allocation` rather than Rebalancing's `Decimal` + `ComputeRequest`/`Response` — there is no tax-lot math here (`models.py`).

## Don't read
- `__pycache__/`
- `Testing/` — gitignored pytest suite.
- `Master_testing/` — runner + captured results, not source.
