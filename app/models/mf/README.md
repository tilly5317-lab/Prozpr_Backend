# Mutual Fund Models Guide

This folder stores mutual fund entities and history.

## What this folder does
- Persists mutual-fund transactions.
- Stores NAV history and metadata.
- Stores snapshots used for portfolio analytics.

## Key files
- `mf_transaction.py`, `mf_sip_mandate.py`
- `mf_nav_history.py`, `mf_fund_metadata.py`, `mf_fund_rating.py`
- `portfolio_allocation_snapshot.py`, `user_investment_list.py`
- `mf_aa_import.py`

## Catalog vs ratings
- `mf_fund_metadata` holds source-fed identity fields only (scheme code, ISIN,
  name, AMC, category, plan/option type, active flag).
- `mf_fund_ratings` holds curated/dynamic data (SEBI risk class, our ratings,
  fee schedule, exit-load terms, sector-mix percentages); 1:1 with metadata
  via `scheme_code` (and mirrors `isin` for cross-source joins).
- Period returns are not persisted on either table — derive them from
  `mf_nav_history`.

## Data Flow
```mermaid
flowchart LR
    A[Ingestion and services] --> B[MF models]
    B --> C[MF tables]
    C --> D[Portfolio analysis]
    D --> E[User and advisor output]
```
