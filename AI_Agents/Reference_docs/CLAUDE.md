# AI_Agents/Reference_docs

Canonical home for reference documents consumed by AI-module pipelines (skill-prompt sources, market-commentary cache, fund-house outlooks, etc.). Files here are read at runtime by agents under `AI_Agents/src/`.

## Files

- `market_commentary_latest.md` — daily-refreshed Indian macro commentary. Written by `app/services/ai_bridge/market_commentary_service.py` (which drives `AI_Agents/src/market_commentary/main.MarketCommentaryAgent`). Read by `AI_Agents/src/portfolio_query/` for the "Fund House Market Commentary" context block.
- `market_commentary_latest.json` — `MacroSnapshot` cache backing the `.md` (1-hour cache TTL via `MARKET_COMMENTARY_CACHE_MAX_AGE_SEC`).
- `prozpr_fund_ranking_may_2026.csv` — Prozpr fund-ranking reference table. Recommended funds carry `rank ≥ 1` and a `selection_reason`; rank-blank rows are funds the data team evaluated but rejected, with per-row `*_reason` columns explaining the call. Consumed by `app/services/ai_bridge/rebalancing/fund_rank.py` (recommended funds via `get_fund_ranking`, rejection text via `get_rejection_reasons`).
**Note:** the thesis `.md` files below are **directional, client-safe documents** — they explain the philosophy and approach but deliberately omit proprietary tuning (exact weights, bands, thresholds, formulas, internal step mechanics). For engine-true numbers, read the code, not these docs.

- `Asset_Allocation.md` — directional thesis for the goal-based *ideal* allocation engine (`AI_Agents/src/asset_allocation_pydantic/`, with engine-true behaviour in `tables.py` and `steps/`); loaded as context for chat modules answering allocation questions. Thesis v1.3.
- `Practical_Asset_Allocation.md` — directional thesis for the *practical* allocation engine (`AI_Agents/src/practical_asset_allocation/pipeline.py`), which translates ideal targets into a holdings-aware plan (ELSS freeze, direct-stock/PMS caps, excess-concentration flag). Loaded as context for chat questions about how holdings shape the plan. Thesis v1.0.
- `Risk_Profiling.md` — directional thesis for the risk-profiling engine (`AI_Agents/src/risk_profiling/`, engine-true logic in `scoring.py` / `models.py`), explaining how the risk score blends capacity and willingness and when a divergence gap is flagged. Loaded as context for chat questions about why the score is what it is. Thesis v1.0.
- `Rebalancing.md` — directional thesis for the goal-based rebalancing engine (`AI_Agents/src/Rebalancing/`, engine-true behaviour in `config.py`, `tables.py`, `steps/`, `rationales.py`; technical specs in `Rebalancing/Reference_docs/`); loaded as context for chat modules answering rebalancing questions. Covers the practical pre-stage, frozen ELSS / direct holdings, and the reduce-direct-stocks instruction. Thesis v1.0.
- `Cashflow_Statement.md` — directional thesis for the cashflow / goal-planning engine (`AI_Agents/src/cashflow_statement/engine/`); loaded as context for chat questions about plan feasibility, retirement corpus, and goal funding. Reflects post-retirement-goal funding behaviour. Thesis v1.0.

## Conventions

- Treat files here as **runtime data**, not committed source. Agents may overwrite them on a schedule.
- Add a new reference doc only when at least one AI module needs it as input.

## Don't read

- `*.json`, `*.md` cached artifacts when reviewing for code changes — they're outputs, not source.
