# Mutual Fund Models Guide

This folder stores mutual fund entities and history.

## What this folder does
- Persists mutual-fund transactions.
- Stores NAV history and metadata.
- Stores snapshots used for portfolio analytics.

## Key files
- `mf_transaction.py`, `mf_sip_mandate.py`
- `mf_nav_history.py`, `mf_fund_metadata.py`
- `portfolio_allocation_snapshot.py`, `user_investment_list.py`
- `mf_aa_import.py`

## Data Flow
```mermaid
flowchart LR
    A[Ingestion and services] --> B[MF models]
    B --> C[MF tables]
    C --> D[Portfolio analysis]
    D --> E[User and advisor output]
```
