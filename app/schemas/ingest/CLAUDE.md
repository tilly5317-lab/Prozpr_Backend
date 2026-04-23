# app/schemas/ingest/

Pydantic models for inbound Account Aggregator (AA) and Finvu MF/portfolio ingestion payloads.
Defines the data contracts between the ingestion routers/services and external AA feed formats.

## Files

- `finvu.py` — `FinvuBucketInput`, `FinvuPortfolioSyncRequest`, `FinvuPortfolioSyncResponse`
- `mf_aa.py` — `MfAaNormalizeOneResponse`, `MfAaNormalizePendingRequest`, `MfAaNormalizePendingResponse`

## Data contract

- `FinvuPortfolioSyncRequest` → `FinvuPortfolioSyncResponse` — consumed by `app/routers/portfolio.py`
  and `app/services/finvu_portfolio_sync.py`.
- `MfAaNormalizePendingRequest` → `MfAaNormalizePendingResponse` — consumed by `app/routers/mf_ingest.py`.
- `MfAaNormalizeOneResponse` — single-record normalization response; consumed by `app/routers/mf_ingest.py`.

## Depends on

- `app/services/finvu_portfolio_sync` — service that drives the Finvu sync flow.
- `app/services/mf_aa_normalizer` — service that normalizes pending AA MF imports.

## Don't read

- `__pycache__/`.

## Refresh

If stale, run `/refresh-context` from this folder.
