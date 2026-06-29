# app/domains/portfolio/ — portfolio container, allocations, holdings, history, net-worth/NAV series

## Layers

- **models/** — `Portfolio` (+ `PortfolioAllocation` / `PortfolioHolding` / `PortfolioHistory` in one file); `UserPortfolioNavHistory` (daily series); `PortfolioNetworthJob` (backfill lifecycle row the UI polls).
- **schemas/** — portfolio + history/nav-history payloads.
- **routers/** — `/portfolio` (detail, allocations, holdings, history, nav-history, TWR, net-worth build/status; deprecated `/finvu/sync`).
- **services/** — `portfolio_service`, `nav_history_service`, `networth_history_service`, `benchmark_service` (portfolio-vs-Nifty-50 chart), `portfolio_query_service` (gateway to the `AI_Agents` portfolio_query agent).

## Gotchas & invariants

- Two daily-series builders, easy to confuse: `nav_history_service` SYNTHESISES a placeholder NAV curve (interpolated `return_1y/3y/5y` + noise, NOT transaction-true); `networth_history_service` is the REAL `mf_transaction` × `mf_nav_history` series — extend the latter (`services/nav_history_service.py`, `services/networth_history_service.py`).
- Net-worth backfill is a two-phase background job (`run_networth_backfill`): Phase A ensures NAV history back to each fund's first txn, Phase B computes the series; writes a polled `portfolio_networth_jobs` row (`services/networth_history_service.py`).
- Portfolio XIRR direction is driven by `transaction_type` (BUY/SWITCH_IN outflow, SELL/SWITCH_OUT inflow), NOT the amount's sign (`abs(amount)` is magnitude). A prior bug matched non-existent "REDEEM" types (`services/portfolio_query_service.py`, `_compute_portfolio_xirr`).
- Canonical portfolio XIRR is `mutual_funds.xirr_service.compute_portfolio_xirr` (what `benchmark_service` reuses). `portfolio_query_service._xirr` is a divergent hand-rolled Newton-Raphson — prefer the shared service for new portfolio code (`services/portfolio_query_service.py`).

## Don't read

- `__pycache__/`, `tests/`.
