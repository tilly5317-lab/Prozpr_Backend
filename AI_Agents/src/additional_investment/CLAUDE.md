# AI_Agents/src/additional_investment — deploy fresh money (lumpsum/SIP) into specific funds

Pure-Python engine: given a deploy amount (lumpsum or monthly SIP) plus the customer's per-bucket subgroup allocation and goal-funding status, it picks specific funds to BUY (never sells). Distinct from `asset_allocation_pydantic` (target mix only) and `Rebalancing` (moves existing money, buy+sell) — this only adds new money. Emergency is always excluded; if medium-term goals are funded (or there are none) it weights subgroups by their `long_term` amount, else by `total − emergency`, renormalises to a ratio, and splits the deploy amount by it.

## Entry / contract
- Entry `run_additional_investment(inp: AdditionalInvestmentInput) → AdditionalInvestmentOutput`.
- Input (caller-populated; the engine imports no peer agent): deploy amount + `Cadence`, per-subgroup bucket amounts (`SubgroupBucketAmounts`, mirrors `asset_allocation_pydantic`'s `AggregatedSubgroupRow`), a `medium_term_fulfilled` bool, the ranked fund list, current holdings, resulting corpus + cap config.
- Output: `SubgroupTarget` table, the BUY list (`FundBuy`), `branch_used`, and `deployed_inr`/`undeployed_inr`.

## Files
- `__init__.py` — public re-exports (entry + I/O models).
- `models.py` — pydantic I/O (`AdditionalInvestmentInput`/`AdditionalInvestmentOutput`, `SubgroupBucketAmounts`, `RankedFund`, `Holding`, `FundBuy`, `SubgroupTarget`, `Cadence`, `BranchUsed`).
- `ratio.py` — the two-branch subgroup split (`compute_targets`).
- `selection.py` — BUY-only, holdings-aware fund selection (`select_funds`).
- `pipeline.py` — entry orchestrator + SIP cadence framing.
- `Testing/` — pytest suite (gitignored).

## Gotchas & invariants
- Pure engine: no LLM, no I/O, no cross-agent imports. Caps are passed IN (caller sources them from `Rebalancing/tables.py`) — not hardcoded here, to keep one source of truth.
- BUY-only + holdings-aware: tops up an acceptable existing holding first (held, not force-exit, rank present or rating ≥ 5), else the rank-1 fund; per-fund cap on resulting corpus; overflow spills rank-1 → rank-2 → rank-3; amounts rounded down to ₹100 (`selection.py`).
- Can under-deploy: when caps bind and a subgroup lacks funds, `sum(buys) < deploy_amount`; the gap is surfaced as `undeployed_inr`, never silently dropped (`pipeline.py`).
- Cadence does not change the ratio — SIP applies the same per-subgroup ratio to the monthly amount; only the `monthly_amount_inr` framing differs.

## Testing
- `PYTHONPATH=AI_Agents/src pytest AI_Agents/src/additional_investment/Testing -v` — both branches, emergency exclusion, renormalisation, rank-1 mapping, caps/overflow, holdings top-up, cadence, and under-deploy.

## Don't read
- `__pycache__/`
- `Testing/` — gitignored pytest suite.
