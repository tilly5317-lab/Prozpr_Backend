# app/domains/portfolio/ — top-level portfolio container + allocations + holdings + history + NAV history

Top-level portfolio container + allocations + holdings + history + nav history.

## Layers

- **models/** — Portfolio (+ PortfolioAllocation, PortfolioHolding, PortfolioHistory in the same file) + UserPortfolioNavHistory
- **schemas/** — portfolio + history payloads
- **routers/** — /portfolio router (primary portfolio, history)
- **services/** — portfolio_service, nav_history_service (NOTE: synthesises a placeholder NAV curve — not transaction-true), benchmark_service (money-weighted portfolio-vs-Nifty-50: cumulative-return lines + benchmark XIRR; reuses mutual_funds TRI/NAV + financial_primitives.xirr)

## Don't read

- `__pycache__/`.
