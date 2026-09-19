# app/domains/privacy/ — DPDP data-principal rights (consent, access, erasure, grievance)

## Entry / contract
- HTTP only: `privacy_router` (registered in `app/routers/__init__.py`). Every route is scoped to the calling user — there is deliberately **no admin surface**, because the rights belong to the person, not to an operator.
- `privacy_scheduler` is the one background entry: a daily job that purges erased accounts, then applies retention.

## Layers
- **models/** — `consent.py`: `consent_records` (append-only ledger), `privacy_policy_versions`, `grievances`, `deleted_user_tombstones`.
- **schemas/** — notice, consent, export, erasure and grievance payloads.
- **routers/** — notice, consent grant/withdraw, access/export, erasure, grievance.
- **services/** — `consent_service`, `export_service`, `erasure_service`, `privacy_scheduler`, and `user_graph` (the shared row walk both access and erasure run on).

## Gotchas & invariants
- **The consent ledger is APPEND-ONLY — withdrawal is a new row, never an update** (`models/consent.py`). The obligation is to show what a person agreed to *and when*, revoked items included; an UPDATE destroys exactly the evidence the ledger exists to hold. The cost: "their current position on purpose P" is a query for the latest row per purpose, not a column read.
- **Erasure is two-stage: tombstone now, purge after the grace window** (`services/erasure_service.py`). Identity columns are overwritten and the credential destroyed inside the request, so the account stops authenticating immediately; the rows and the unreferenced files go later. The delay is not reluctance — erasure is irreversible, "delete my account" is a common misclick, and a purge in-request would hold a transaction across S3 calls.
- **`deleted_user_tombstones` survives the purge on purpose** — so restoring a database backup cannot silently resurrect someone who asked to be erased.
- **Export and erasure MUST share `user_graph`.** If a table is reachable for one it is reachable for the other; two hand-kept table lists drift, and an export that describes a smaller system than the deletion empties is a compliance defect.
- **Walk the graph — never hand-order a delete list.** Nothing cascades from `users`: 47 foreign keys reference it and the row-holding ones are `NO ACTION`, so `DELETE FROM users` aborts on the first constraint. Counting only direct `user_id` FKs once found ~61.5k rows for 44 accounts; the proper walk found 43 tables (`services/user_graph.py`).
- **The scheduler defaults to a DRY RUN.** `PRIVACY_SCHEDULER_ENABLED` gates it and `PRIVACY_RETENTION_APPLY` gates actual removal, because everything here is irreversible and the sensible first deployment is one that only reports what it would have removed. Follows the existing `*_SCHEDULER_ENABLED` pattern.

## Don't read
- `__pycache__/`, `tests/`.
