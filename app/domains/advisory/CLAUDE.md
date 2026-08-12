# app/domains/advisory/ — IPS (investment policy statements), meeting notes, discovery browsing, team-call booking

## Layers

- **models/** — `InvestmentPolicyStatement`; `MeetingNote` + `MeetingNoteItem`. (Team-call has no table — the meeting lives in Zoom.)
- **schemas/** — `ips`, `meeting_note`, `discovery`, `team_call` payloads.
- **routers/** — four thin HTTP surfaces, one per file:
  - `/ips` — read the current investment policy statement, generate a new one from the stored profile tables, list version history.
  - `/meeting-notes` — CRUD on meeting notes and their line items, plus mandate approval.
  - `/discovery` — read-only mutual-fund catalogue browsing (funds, sectors, trending, house-view) over `mutual_funds`' `Fund` model; despite the domain name it is not an advisory artefact, it just shares the folder.
  - `/team-call` — books real Zoom meetings for the portfolio page's "Talk to the Prozpr team" card; delegates to `services/zoom_service`.

## Gotchas & invariants

- Only `/team-call` has a `services/` layer (`zoom_service`); the IPS/meeting-notes/discovery routers still hold their CRUD logic directly and persist via the ORM. Promote those to a service before they grow beyond thin CRUD (`routers/ips_router.py`).
- **Team-call keeps no DB row** — the meeting lives in Zoom, cancellation ownership is verified via an agenda marker, and the endpoint returns 503 when the `ZOOM_*` env vars are unset so the frontend falls back to its static link (`services/zoom_service.py`).
- The IPS household-finance view must read scalars through `profile.profile_finance`, never off `investment_profile` columns (the canonical home is `personal_finance_profiles`).

## Don't read

- `__pycache__/`.
