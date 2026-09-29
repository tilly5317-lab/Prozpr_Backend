# AI_Agents/src/additional_investment — deploy fresh money (lumpsum/SIP) into specific funds

Pure-Python engine: splits a deploy amount across allocation subgroups, then picks funds to BUY (never sells) from the ranked list. Goal money first, then the long-term plan (SIP) or long-term deficits (lumpsum). Distinct from `asset_allocation_pydantic` (target mix only) and `Rebalancing` (buy+sell of existing money) — this only adds new money.

## Entry / contract
- Entry `run_additional_investment(inp: AdditionalInvestmentInput) → AdditionalInvestmentOutput`; imports one constant from `Rebalancing.config` (the shared fund-count threshold), no other peer agent.
- Input (caller-populated): deploy amount + cadence, per-subgroup bucket amounts (mirrors `AggregatedSubgroupRow`), optional `current_value_by_subgroup` (`models.py`), `goal_share_inr`/`goal_subgroup`, ranked funds, `investable_corpus_inr` (decides 1-vs-2 funds/subgroup), `exclude_subgroups`. The cap knobs (`cap_pct_by_subgroup`, `*_fund_cap_floor_inr`) and `rebal_buy_isins_by_subgroup` are vestigial since spec 2026-09-24 — retained on the model, ignored by selection.
- Output: `SubgroupTarget` table, BUY list (`FundBuy`), `target_bucket` (a label, not the split driver — `short_term` when the goal share is at least half the deploy amount, else `long_term`), `deployed_inr`/`undeployed_inr`.

## Files
- `pipeline.py` — entry orchestrator: computes the goal-first split, then frames SIP cadence or reconciles lumpsum rounding.
- `ratio.py` — subgroup split: goal money first (`compute_goal_first_targets`), then the long-term column (SIP) or long-term deficits (lumpsum).
- `selection.py` — BUY-only fund selection from the ranking.
- `models.py` — pydantic I/O models; `__init__.py` re-exports the entry + those models.

## Gotchas & invariants
- **Pure engine.** No LLM, no I/O. The one cross-agent import is `Rebalancing.config.SUBGROUP_FUND_COUNT_THRESHOLD_INR` — the 1-vs-2 fund-count threshold single-sourced from rebalancing (`pipeline.py`).
- **One split: goal money first** (`ratio.py::compute_goal_first_targets`). The goal share goes to `goal_subgroup` by name — never weighted by the short-term column, which is empty when the plan holds no short-term money. The rest follows the long-term column (SIP) or deficit-fill against each row's `total − short_term` (lumpsum; the caller removes only the goal-used held short-term money, pro rata across short-term subgroups — excess held short-term money stays, `ainv_engine/service.py::_long_term_holdings`).
- **Fund selection is holding-agnostic.** Each subgroup's target splits equally across its top 1 or 2 ranked funds (rank-1 first), N by `investable_corpus_inr` — 2 at/above ₹50L else 1 (spec 2026-09-24); nearest-₹100 rounding (`selection.py`, `pipeline.py:_funds_per_subgroup`). SIP and lumpsum select identically; the SIP-mirrors-rebalancing path and per-fund caps are retired.
- **`exclude_subgroups` get zero weight/deficit** in `ratio.py`; the share renormalises. Caller policy: `non_mf_equities` (no funds), `tax_efficient_equities` (ELSS lock-in).
- **Can under-deploy.** Fund scarcity (too few ranked funds, or a per-fund share rounding below ₹100) leaves a gap surfaced as `undeployed_inr`, never silently dropped (`pipeline.py`).
- **Cadence doesn't change the ratio.** SIP applies the same split to the monthly amount; only the `monthly_amount_inr` framing differs (`pipeline.py`).
- **Allocation-family I/O shape.** Money is `float` rupees like `practical_asset_allocation`, not Rebalancing's `Decimal` — no tax-lot math (`models.py`).

## Don't read
- `__pycache__/`
- `Testing/` — gitignored pytest suite.
- `Master_testing/` — runner + captured results, not source.
