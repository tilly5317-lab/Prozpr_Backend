# app/schemas/ingest/

Pydantic models for inbound MF / portfolio ingestion payloads — the data contracts between
the ingestion routers/services and external feed formats.

## Files

- `cams.py` — `CamsPdfImportResponse`: result of a CAMS/KFintech CAS PDF upload + ingest.
- `mf_aa.py` — `MfAaNormalizeOneResponse`, `MfAaNormalizePendingRequest`, `MfAaNormalizePendingResponse`
- `finvu.py` — DEPRECATED / SIDELINED. `FinvuBucketInput`, `FinvuPortfolioSyncRequest`,
  `FinvuPortfolioSyncResponse` — back the legacy `POST /portfolio/finvu/sync` route only.

## Data contract

- CAMS upload: multipart `file` + `password` (declared inline on `app/routers/mf_ingest.py`)
  → `CamsPdfImportResponse`. Driven by `app/services/cams_cas_ingest`.
- `MfAaNormalizePendingRequest` → `MfAaNormalizePendingResponse` — consumed by `app/routers/mf_ingest.py`.
- `MfAaNormalizeOneResponse` — single-record normalization response; consumed by `app/routers/mf_ingest.py`.
- `FinvuPortfolioSyncRequest` → `FinvuPortfolioSyncResponse` — legacy; consumed by `app/routers/portfolio.py`
  and `app/services/finvu_portfolio_sync.py` (not on an active path).

## Depends on

- `app/services/cams_cas_ingest` — parses the CAS PDF and writes the AA + MF tables.
- `app/services/mf_aa_normalizer` — normalizes AA / CAS import rows into `mf_transactions`.
- `app/services/finvu_portfolio_sync` — legacy Finvu sync flow (sidelined).

## Don't read

- `__pycache__/`.
