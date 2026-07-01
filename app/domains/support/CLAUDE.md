# app/domains/support/ — in-app issue reports + support email

## Layers

- **models/** — empty (only stale `.pyc`): there is deliberately NO DB table — issue reports persist to the Google Sheet register, not a DB row.
- **schemas/** — `IssueReportResponse` (request arrives as multipart form/file, not a schema).
- **routers/** — `/support` — `POST /support/report-issue`: multipart, validates, persists, fires email as a background task.
- **services/** — `issue_report_service` — derived side effects: screenshot to disk + support email over SMTP.

## Gotchas & invariants

- The Google-Sheet append (`append_to_google_sheet`) is the SOLE issue register and FAILS the request with a 503 when the webhook is unset or unreachable — no DB row, no local fallback, so a report is never silently dropped (`routers/support_router.py:131`; 503 raised in the `except` at :132-140).
- Best-effort side effects only: the optional screenshot save (swallows `OSError`) and the Zoho-SMTP email (`smtp.zoho.com:465`, background task, skipped with a warning when `SMTP_PASSWORD` is unset) never fail the request (`services/issue_report_service.py`).

## Don't read

- `__pycache__/`.
