# AI_Agents/Reference_docs — Reference-doc index

Reference documents read at runtime by agents under `AI_Agents/src/`: skill-prompt sources, market-commentary cache, fund ranking, and directional thesis docs.

## Files

- `ARCHITECTURE.md` — hand-authored architecture walkthrough of `AI_Agents/`: per-agent contracts, deterministic-vs-LLM split, cross-agent data-flow, conventions, landmines. Companion to top-level `docs/ARCHITECTURE.md`.
- `ARCHITECTURE.html` — styled HTML rendering of `ARCHITECTURE.md` (rendered Mermaid, ToC) for browser reading.
- `CHAT_FLOW.html` — styled HTML guide to the AI chat flow (rendered Mermaid) for browser reading.
- `market_commentary_latest.md` — daily Indian macro commentary. Written by `src/market_commentary/` (`main.MarketCommentaryAgent`, driven by `app/domains/market_commentary/services/market_commentary_engine.py`). Read by `src/portfolio_query/` for its commentary context block.
- `market_commentary_latest.json` — `MacroSnapshot` cache backing the `.md` (TTL via `MARKET_COMMENTARY_CACHE_MAX_AGE_SEC`).
- `prozpr_fund_ranking_may_2026.csv` — fund-ranking table. `rank ≥ 1` + `selection_reason` = recommended; rank-blank rows are evaluated-then-rejected, with per-row `*_reason` columns. Consumed by `app/domains/rebalancing/services/rebal_engine/fund_rank.py` (`get_fund_ranking`, `get_rejection_reasons`).

**Thesis `.md` files** (below) are directional, client-safe documents — philosophy and approach only; they deliberately omit proprietary weights, bands, thresholds, and step mechanics. For engine-true numbers, read the code.

- `Asset_Allocation.md` — ideal goal-based allocation engine (`src/asset_allocation_pydantic/`). Thesis v1.3.
- `Practical_Asset_Allocation.md` — practical/holdings-aware engine (`src/practical_asset_allocation/pipeline.py`): ELSS freeze, direct-stock/PMS caps, excess-concentration flag. Thesis v1.0.
- `Risk_Profiling.md` — risk-profiling engine (`src/risk_profiling/`): how the score blends capacity and willingness and when divergence is flagged. Thesis v1.0.
- `Rebalancing.md` — goal-based rebalancing engine (`src/Rebalancing/`): practical pre-stage, frozen ELSS/direct holdings, reduce-direct-stocks instruction. Thesis v1.0.
- `Cashflow_Statement.md` — cashflow/goal-planning engine (`src/cashflow_statement/engine/`): plan feasibility, retirement corpus, goal funding. Thesis v1.0.

## Gotchas & invariants

- Treat files here as runtime data, not committed source — agents may overwrite them on a schedule. Add a new reference doc only when an AI module needs it as input.

## Don't read

- `*.json` / cached `*.md` artifacts when reviewing for code changes — they're outputs, not source.
