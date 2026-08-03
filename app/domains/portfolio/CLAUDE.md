# app/domains/portfolio/ — portfolio container, allocations, holdings, history, net-worth/NAV series

## Layers

- **models/** — `Portfolio` (+ `PortfolioAllocation` / `PortfolioHolding` / `PortfolioHistory` in one file); `UserPortfolioNavHistory` (daily series); `PortfolioNetworthJob` (backfill lifecycle row the UI polls).
- **schemas/** — portfolio + history/nav-history payloads.
- **routers/** — `/portfolio` (detail, allocations, holdings, history, nav-history, TWR, net-worth build/status; deprecated `/finvu/sync`).
- **services/** — `portfolio_service`, `nav_history_service`, `networth_history_service`, `benchmark_service` (portfolio-vs-Nifty-50 chart), `twr_service` (DB adapter feeding the `financial_primitives.twr_wealth_index` kernel for the Returns tab), `allocation_rollup` (the SINGLE source for the Equity/Debt/Others mix — shared by the dashboard donut and chat's current-mix narration in `aa_engine`, so the two can never disagree; callers add their own cash bucket), `portfolio_query_service` (gateway to the `AI_Agents` portfolio_query agent).

## Gotchas & invariants

- Two daily-series builders, easy to confuse: `nav_history_service` SYNTHESISES a placeholder NAV curve (interpolated `return_1y/3y/5y` + noise, NOT transaction-true); `networth_history_service` is the REAL `mf_transaction` × `mf_nav_history` series — extend the latter (`services/nav_history_service.py`, `services/networth_history_service.py`).
- Net-worth backfill is a two-phase background job (`run_networth_backfill`): Phase A ensures NAV history back to each fund's first txn, Phase B computes the series; writes a polled `portfolio_networth_jobs` row (`services/networth_history_service.py`).
- Portfolio XIRR direction is driven by `transaction_type` (BUY/SWITCH_IN outflow, SELL/SWITCH_OUT inflow), NOT the amount's sign (`abs(amount)` is magnitude). A prior bug matched non-existent "REDEEM" types (`services/portfolio_query_service.py`, `_compute_portfolio_xirr`).
- **Per-fund XIRR is a separate lookup from portfolio XIRR.** `_xirr_by_scheme` reads `UserMfLatestSnapshot.xirr_pct` keyed by `scheme_code` — which IS the holding's `ticker_symbol` — and fills `Holding.xirr_pct` so chat can rank and compare individual funds. It returns `{}` on ANY failure by design: a missing XIRR must degrade one line of an answer, never fail the turn, so a broken lookup shows up as the model quietly dropping per-fund XIRR rather than as an error — check the warning log (`services/portfolio_query_service.py`).
- **The market commentary is no longer fetched for every portfolio turn.** `answer_portfolio_query` passes `want_market_commentary` from the classifier's `ctx.tools_needed`; unconditional loading made the model compare an allocation % against a P/E. The agent-side default is `True`, so a new caller that forgets to pass it silently restores the old behaviour (`services/portfolio_query_service.py`).
- Canonical portfolio XIRR is `mutual_funds.services.xirr_service.compute_portfolio_xirr` (what `benchmark_service` reuses). `portfolio_query_service._xirr` is a divergent hand-rolled Newton-Raphson — prefer the shared service for new portfolio code (`services/portfolio_query_service.py`).

## Don't read

- `__pycache__/`, `tests/`.
