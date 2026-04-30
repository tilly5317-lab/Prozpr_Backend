# app/models/mf/

Mutual fund domain tables covering fund metadata, NAV history, user transactions,
SIP mandates, AA-import batches, portfolio snapshots, and watchlists.
Column-level detail: `README_DATABASE_SCHEMA.md`.

## Files

- `enums.py` — MF-domain enum types (no ORM table)
- `mf_aa_import.py` — `MfAaImport`, `MfAaSummary`, `MfAaTransaction`
- `mf_fund_metadata.py` — `MfFundMetadata`
- `mf_nav_history.py` — `MfNavHistory`
- `mf_sip_mandate.py` — `MfSipMandate`
- `mf_transaction.py` — `MfTransaction`
- `portfolio_allocation_snapshot.py` — `PortfolioAllocationSnapshot`
- `user_investment_list.py` — `UserInvestmentList`

## Tables

- `mf_fund_metadata` — `MfFundMetadata`; reference data for each mutual fund scheme. Relationships: has many MfNavHistory rows.
- `mf_nav_history` — `MfNavHistory`; daily NAV time-series per scheme. Relationships: belongs to MfFundMetadata.
- `mf_transactions` — `MfTransaction`; ledger of buy/sell/switch transactions. Relationships: belongs to User, belongs to MfFundMetadata, optionally belongs to MfSipMandate and MfAaImport.
- `mf_sip_mandates` — `MfSipMandate`; active SIP instructions per user and scheme. Relationships: belongs to User, belongs to MfFundMetadata; has many MfTransactions.
- `mf_aa_imports` — `MfAaImport`; a single account-aggregator import batch for a user. Relationships: belongs to User; has many MfAaSummaries, has many MfAaTransactions.
- `mf_aa_summaries` — `MfAaSummary`; fund-level summary row within an AA import. Relationships: belongs to MfAaImport.
- `mf_aa_transactions` — `MfAaTransaction`; transaction-level row within an AA import. Relationships: belongs to MfAaImport.
- `portfolio_allocation_snapshots` — `PortfolioAllocationSnapshot`; point-in-time allocation snapshot for a user's MF portfolio. Relationships: belongs to User.
- `user_investment_lists` — `UserInvestmentList`; user-curated watchlist or investment list entries. Relationships: belongs to User.

## Depends on

- `app/models/user.py` — User hub; several tables carry a `users.id` foreign key.

## Don't read

- `__pycache__/`.

## Refresh

If stale, run `/refresh-context` from this folder.
