# app/domains/equities/ — company metadata, prices, transactions

Company metadata, prices, transactions.

## Layers

- **models/** — `CompanyMetadata`, `StockPriceHistory`, `StockTransaction`, `StockTransactionType` enum.
  File names use the new `equity_*` naming (per the proposed structure), but the **ORM class names stay
  `Stock*`** — those are referenced by string from `User.stock_transactions` (and the DB tables are
  `stock_*`); a class-name rename would need a coordinated DB migration. The class-name swap is a
  follow-up; the file-level rename is done.
- **schemas/** — (empty — to be populated when equity endpoints land)
- **routers/** — (empty)
- **services/** — (empty)

## Don't read

- `__pycache__/`.
