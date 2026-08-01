# app/domains/advisory/ — IPS (investment policy statements), meeting notes, discovery helpers

## Layers

- **models/** — `InvestmentPolicyStatement`; `MeetingNote` + `MeetingNoteItem`.
- **schemas/** — `ips`, `meeting_note`, `discovery` payloads.
- - **routers/** — three thin HTTP surfaces, one per file:
  - `/ips` — read the current investment policy statement, generate a new one from the stored profile tables, list version history.
  - `/meeting-notes` — CRUD on meeting notes and their line items, plus mandate approval.
  - `/discovery` — read-only mutual-fund catalogue browsing (funds, sectors, trending, house-view) over `mutual_funds`' `Fund` model; despite the domain name it is not an advisory artefact, it just shares the folder.

## Gotchas & invariants

- No `services/` layer — routers hold the logic directly and persist via the ORM. Promote to a service before this domain grows beyond thin CRUD (`routers/ips_router.py`).
- The IPS household-finance view must read scalars through `profile.profile_finance`, never off `investment_profile` columns (the canonical home is `personal_finance_profiles`).

## Don't read

- `__pycache__/`.
