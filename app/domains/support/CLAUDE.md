# app/domains/support/ — in-app issue reports + support email

## Layers

- **models/** — `IssueReport` (the DB row is the source of truth; screenshot file + email are derived).
- **schemas/** — `IssueReportResponse` (request arrives as multipart form/file, not a schema).
- **routers/** — `/support` — `POST /support/report-issue`: multipart, validates, persists, fires email as a background task.
- **services/** — `issue_report_service` — derived side effects: screenshot to disk + support email over SMTP.

## Gotchas & invariants

- Side effects never fail the request: the row commits first, then screenshot save and email (background task) run synchronously and swallow errors. Email uses Zoho SMTP (`smtp.zoho.com:465`), skipped with a warning when `SMTP_PASSWORD` is unset (`services/issue_report_service.py`).

## Don't read

- `__pycache__/`.
