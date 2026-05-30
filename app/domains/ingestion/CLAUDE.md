# app/domains/ingestion/ — all external-data adapters: CAMS / KFintech CAS PDF upload, AA normalizer, SimBanks ConnectHub XML, and (sidelined) Finvu account-aggregator

All external-data adapters: cams / kfintech cas pdf upload, aa normalizer, simbanks connecthub xml, and (sidelined) finvu account-aggregator.

## Layers

- **models/** — (no ORM — uses other domains' tables)
- **schemas/** — cams / mf_aa / finvu / simbanks payloads
- **routers/** — /mf-ingest router (CAMS PDF upload, AA-import normalize, mfapi.in refresh), /simbanks router
- **services/** — cams_cas_ingest, mf_aa_normalizer, simbanks_service, finvu_portfolio_sync (DEPRECATED)

## Don't read

- `__pycache__/`.
