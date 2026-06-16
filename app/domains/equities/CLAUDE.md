# app/domains/equities/ — company metadata, prices, transactions

## Layers

- **models/** — `CompanyMetadata`, `StockPriceHistory`, `StockTransaction` + `StockTransactionType` enum. Only this layer exists today; `schemas/`, `routers/`, `services/` arrive when equity endpoints land.

## Gotchas & invariants

- Files use the new `equity_*` names but the **ORM classes stay `Stock*`** and tables stay `stock_*` — `User.stock_transactions` references them by string. Renaming the classes needs a coordinated DB migration; only the file-level rename is done (`models/equity_transaction.py`).

## Don't read

- `__pycache__/`.
