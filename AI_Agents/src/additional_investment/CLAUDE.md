# AI_Agents/src/additional_investment — deploy fresh money (lumpsum/SIP) into specific funds

Pure-Python engine: splits a deploy amount across allocation subgroups, then picks funds to BUY (never sells) from the ranked list. Lumpsum-with-holdings fills allocation deficits; SIP follows the ideal mix. Distinct from `asset_allocation_pydantic` (target mix only) and `Rebalancing` (buy+sell of existing money) — this only adds new money.

## Entry / contract
- Entry `run_additional_investment(inp: AdditionalInvestmentInput) → AdditionalInvestmentOutput`; imports one constant from `Rebalancing.config` (the shared fund-count threshold), no other peer agent.
- Input (caller-populated): deploy amount + cadence, per-subgroup bucket amounts (mirrors `AggregatedSubgroupRow`), optional `current_value_by_subgroup` (`models.py`), `short_term_fulfilled`/`medium_term_fulfilled`, ranked funds, `investable_corpus_inr` (decides 1-vs-2 funds/subgroup), `exclude_subgroups`. The cap knobs (`cap_pct_by_subgroup`, `*_fund_cap_floor_inr`) and `rebal_buy_isins_by_subgroup` are vestigial since spec 2026-09-24 — retained on the model, ignored by selection.
- Output: `SubgroupTarget` table, BUY list (`FundBuy`), `target_bucket` (in deficit mode a label — the dominant horizon of deployed money, not the split driver), `deployed_inr`/`undeployed_inr`.

## Files
- `pipeline.py` — entry orchestrator: split-mode switch + SIP cadence framing.
- `ratio.py` — subgroup split: legacy bucket targeting + deficit-fill.
- `selection.py` — BUY-only fund selection from the ranking.
- `models.py` — pydantic I/O models; `__init__.py` re-exports the entry + those models.

## Gotchas & invariants
- **Pure engine.** No LLM, no I/O. The one cross-agent import is `Rebalancing.config.SUBGROUP_FUND_COUNT_THRESHOLD_INR` — the 1-vs-2 fund-count threshold single-sourced from rebalancing (`pipeline.py`).
- **Two split modes** (`pipeline.py:47`): LUMPSUM with `current_value_by_subgroup` set ⇒ deficit-fill; SIP, or lumpsum without holdings ⇒ legacy bucket targeting.
- **Deficit-fill** (`compute_deficit_targets`): ideal = each eligible subgroup's `total` (caller runs PAA at corpus + deploy — the post-investment ideal); the deploy splits across `max(0, ideal − current)` deficits; all at/above ideal ⇒ fall back to ideal ratios. Iterate ideal rows, not holdings — a held subgroup with no ideal row gets no buy, no error.
- **Bucket targeting (legacy path).** `select_target_bucket` picks the nearest unfunded horizon short → medium → long (long is the fallback); `compute_targets` weights subgroups by that bucket's column. Emergency is never a target; a targeted bucket with no allocation ⇒ fully undeployed (`ratio.py`).
- **Fund selection is holding-agnostic.** Each subgroup's target splits equally across its top 1 or 2 ranked funds (rank-1 first), N by `investable_corpus_inr` — 2 at/above ₹50L else 1 (spec 2026-09-24); nearest-₹100 rounding (`selection.py`, `pipeline.py:_funds_per_subgroup`). SIP and lumpsum select identically; the SIP-mirrors-rebalancing path and per-fund caps are retired.
- **`exclude_subgroups` get zero weight/deficit** in `ratio.py`; the share renormalises. Caller policy: `non_mf_equities` (no funds), `tax_efficient_equities` (ELSS lock-in).
- **Can under-deploy.** Fund scarcity (too few ranked funds, or a per-fund share rounding below ₹100) leaves a gap surfaced as `undeployed_inr`, never silently dropped (`pipeline.py`).
- **Cadence doesn't change the ratio.** SIP applies the same split to the monthly amount; only the `monthly_amount_inr` framing differs (`pipeline.py`).
- **Allocation-family I/O shape.** Money is `float` rupees like `practical_asset_allocation`, not Rebalancing's `Decimal` — no tax-lot math (`models.py`).

## Don't read
- `__pycache__/`
- `Testing/` — gitignored pytest suite.
- `Master_testing/` — runner + captured results, not source.
