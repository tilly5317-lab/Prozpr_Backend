# AI_Agents/src/additional_investment — deploy fresh money (lumpsum/SIP) into specific funds

Pure-Python engine: splits a deploy amount across allocation subgroups, then picks funds to BUY (never sells) from the ranked list. Lumpsum-with-holdings fills allocation deficits; SIP follows the ideal mix and mirrors the latest rebalancing run's BUY funds. Distinct from `asset_allocation_pydantic` (target mix only) and `Rebalancing` (buy+sell of existing money) — this only adds new money.

## Entry / contract
- Entry `run_additional_investment(inp: AdditionalInvestmentInput) → AdditionalInvestmentOutput`; imports no peer agent.
- Input (caller-populated): deploy amount + cadence, per-subgroup bucket amounts (mirrors `AggregatedSubgroupRow`), optional `current_value_by_subgroup` (`models.py`), `short_term_fulfilled`/`medium_term_fulfilled`, ranked funds, cap percentages, `exclude_subgroups`, optional `rebal_buy_isins_by_subgroup` (SIP mirror — latest rebalancing run's BUY ISINs).
- Output: `SubgroupTarget` table, BUY list (`FundBuy`), `target_bucket` (in deficit mode a label — the dominant horizon of deployed money, not the split driver), `deployed_inr`/`undeployed_inr`.

## Files
- `__init__.py` — public re-exports (entry + I/O models).
- `models.py` — pydantic I/O models.
- `ratio.py` — subgroup split: legacy bucket targeting (`select_target_bucket`, `compute_targets`) + deficit-fill (`compute_deficit_targets`, `dominant_bucket`).
- `selection.py` — BUY-only fund selection from the ranking: `select_funds` (lumpsum, caps + rank-spill) and `select_funds_sip` (SIP, rebalancing mirror / rank-1 fallback).
- `pipeline.py` — entry orchestrator: split-mode switch + SIP cadence framing.
- `Testing/` — pytest suite (gitignored).

## Gotchas & invariants
- **Pure engine.** No LLM, no I/O, no cross-agent imports. Cap percentages are passed IN (caller sources `Rebalancing/tables.py`) — one source of truth (`models.py`).
- **Two split modes** (`pipeline.py:20`): LUMPSUM with `current_value_by_subgroup` set ⇒ deficit-fill; SIP, or lumpsum without holdings ⇒ legacy bucket targeting.
- **Deficit-fill** (`compute_deficit_targets`): ideal = each eligible subgroup's `total` (caller runs PAA at corpus + deploy — the post-investment ideal); the deploy splits across `max(0, ideal − current)` deficits; all at/above ideal ⇒ fall back to ideal ratios. Iterate ideal rows, not holdings — a held subgroup with no ideal row gets no buy, no error.
- **Bucket targeting (legacy path).** `select_target_bucket` picks the nearest unfunded horizon short → medium → long (long is the fallback); `compute_targets` weights subgroups by that bucket's column. Emergency is never a target; a targeted bucket with no allocation ⇒ fully undeployed (`ratio.py`).
- **Lumpsum fund selection is holding-agnostic.** BUYs come purely from the ranking (rank-1 first, overflow spills down); nearest-₹100 rounding (`selection.py`).
- **SIP mirrors the rebalancing plan** (spec 2026-07-05 + cap amendment 2026-07-06, `select_funds_sip`): caller-supplied `rebal_buy_isins_by_subgroup` gets an EQUAL split per subgroup (per-subgroup ISIN match — wrong-subgroup/stale ISINs drop out), each buy capped at `max(cap_pct × monthly, sip_fund_cap_floor_inr)`; overflow — or the whole share when there are no candidates — walks down the ranking under the same cap. Dust shares consolidate into the first candidate; sub-₹100 remainders go to `undeployed_inr` (no ₹0 rows). Equal-split rounding can overshoot the monthly amount — ACCEPTED trade-off (characterization-tested), do not "fix".
- **Per-fund cap keys off the DEPLOY amount, not corpus.** Both cadences: `cap_amt = max(pct × deploy_amount, floor)` (`selection.py`, `_cap_amount` + floor) — floors are caller-sourced from `Rebalancing/config` (`AINV_SIP_FUND_CAP_FLOOR_INR` ₹10k, `AINV_LUMPSUM_FUND_CAP_FLOOR_INR` ₹40k) so a small deployment never fragments; engine defaults are 0 (percentage-only). Deliberate divergence from Rebalancing.
- **`exclude_subgroups` get zero weight/deficit** in `ratio.py`; the share renormalises. Caller policy: `non_mf_equities` (no funds), `tax_efficient_equities` (ELSS lock-in).
- **Can under-deploy.** Caps or fund scarcity leave a gap surfaced as `undeployed_inr`, never silently dropped (`pipeline.py`).
- **Cadence doesn't change the ratio.** SIP applies the same split to the monthly amount; cadence switches the selector (`select_funds_sip` vs `select_funds`) and the `monthly_amount_inr` framing, never the subgroup split (`pipeline.py`).
- **Allocation-family I/O shape.** Money is `float` rupees like `practical_asset_allocation`, not Rebalancing's `Decimal` — no tax-lot math (`models.py`).

## Don't read
- `__pycache__/`
- `Testing/` — gitignored pytest suite.
- `Master_testing/` — runner + captured results, not source.
