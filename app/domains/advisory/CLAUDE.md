# app/domains/advisory/ — IPS (investment policy statements), meeting notes, discovery helpers

Ips (investment policy statements), meeting notes, discovery helpers.

## Layers

- **models/** — InvestmentPolicyStatement, MeetingNote + MeetingNoteItem
- **schemas/** — ips, meeting_note, discovery payloads
- **routers/** — /ips, /meeting-notes, /discovery routers
- **services/** — (empty — routers are thin; logic to be promoted later)

## Don't read

- `__pycache__/`.
