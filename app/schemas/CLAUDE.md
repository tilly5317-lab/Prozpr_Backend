# app/schemas/ — Pydantic request/response models

Pydantic schemas for HTTP requests and responses. Not ORM. Three subfolders
group the more complex API surfaces; flat files cover stable single-domain
contracts.

## Child modules

- **profile/** — CompleteProfile API payloads (read + update).
- **ai_modules/** — request/response bodies for the `/ai-modules` agent test
  routes.
- **ingest/** — Finvu / AA ingestion payloads.

## Files at this level

- `auth.py` — login, token, and registration payloads.
- `chat.py` — chat session and message payloads.
- `portfolio.py` — portfolio read/write schemas.
- `goal.py` — goals and contribution payloads.
- `onboarding.py` — onboarding step payloads.
- `discovery.py` — client discovery payloads.
- `family.py` — family-linking payloads.
- `linked_account.py` — linked-account payloads.
- `ips.py` — investment policy statement payloads.
- `meeting_note.py` — meeting note payloads.
- `notification.py` — notification payloads.
- `rebalancing.py` — rebalancing recommendation payloads.
- `simbanks.py` — SimBanks sync payloads.

## Don't read

- `__pycache__/`.
