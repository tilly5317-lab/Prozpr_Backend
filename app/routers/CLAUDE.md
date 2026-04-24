# app/routers/ — HTTP routers

Routers expose the FastAPI HTTP surface. Each file defines a router for one
area; they are assembled in `__init__.py` and mounted at the `/api/v1` prefix
by `app/main.py`.

## Child modules

- **ai_modules/** — per-agent HTTP endpoints for exercising AI_Agents
  orchestrators directly (intent, market, portfolio query, allocation, drift,
  MF status, risk).

## Files at this level

- `health.py` — `/health` liveness probe.
- `auth.py` — `/auth` login, tokens, registration.
- `onboarding.py` — `/onboarding` early profile, other assets, completion flag.
- `profile.py` — `/profile` full CompleteProfile read/update.
- `goals.py` — `/goals` goals, contributions, holdings.
- `portfolio.py` — `/portfolio` primary portfolio, allocations, holdings,
  history, Finvu ingest.
- `chat.py` — `/chat` sessions, messages, uploads; send delegates to ChatBrain.
- `meeting_notes.py` — notes creation and retrieval.
- `notifications.py` — alert delivery endpoints.
- `discovery.py` — client discovery helpers.
- `rebalancing.py` — rebalancing recommendations.
- `ips.py` — `/ips` investment policy statements.
- `linked_accounts.py` — linked-account management.
- `family.py` — family linking; `X-Family-Member-Id` header to act as member.
- `simbanks.py` — SimBanks ConnectHub sync → portfolio + MF.
- `mf_ingest.py` — mutual-fund data ingestion routes.
- `__init__.py` — assembles `all_routers`.

## Don't read

- `__pycache__/`.

## Refresh

If this file looks stale after a structural change, run `/refresh-context`
from this folder.
