# app/domains/advisory/ — IPS (investment policy statements), meeting notes, discovery helpers

## Layers

- **models/** — `InvestmentPolicyStatement`; `MeetingNote` + `MeetingNoteItem`.
- **schemas/** — `ips`, `meeting_note`, `discovery` payloads.
- **routers/** — `/ips`, `/meeting-notes`, `/discovery`.

## Gotchas & invariants

- No `services/` layer — routers hold the logic directly and persist via the ORM. Promote to a service before this domain grows beyond thin CRUD (`routers/ips_router.py`).
- The IPS household-finance view must read scalars through `profile.profile_finance`, never off `investment_profile` columns (the canonical home is `personal_finance_profiles`).

## Don't read

- `__pycache__/`.
