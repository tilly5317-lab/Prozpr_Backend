# app/models/stocks/

Equity domain tables covering company reference data, historical price series, and
user stock transactions. Column-level detail: `README_DATABASE_SCHEMA.md`.

## Files

- `enums.py` — stocks-domain enum types (no ORM table)
- `company_metadata.py` — `CompanyMetadata`
- `stock_price_history.py` — `StockPriceHistory`
- `stock_transaction.py` — `StockTransaction`

## Tables

- `company_metadata` — `CompanyMetadata`; reference data for listed companies (symbol, sector, exchange). Relationships: has many StockPriceHistory rows.
- `stock_price_history` — `StockPriceHistory`; daily OHLCV price series per company symbol. Relationships: belongs to CompanyMetadata.
- `stock_transactions` — `StockTransaction`; user buy/sell equity transaction ledger. Relationships: belongs to User, belongs to CompanyMetadata.

## Depends on

- `app/models/user.py` — User hub; `stock_transactions` carries a `users.id` foreign key.

## Don't read

- `__pycache__/`.
