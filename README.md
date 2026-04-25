# Ask Tilly — Backend

FastAPI API on **PostgreSQL** (SQLAlchemy async). AI behaviour is delegated to the bundled **`AI_Agents`** package via `sys.path` injection; integration stays in `app/services`, not by editing `AI_Agents` internals.

**Also read:** [README_DATABASE_SCHEMA.md](./README_DATABASE_SCHEMA.md) (tables/columns). **Run:** from this folder, `uvicorn main:app --reload` → [`main.py`](./main.py) imports `app` from [`app/main.py`](./app/main.py).

---

## Repository layout

Paths are relative to **`backend/`** (this directory).

```
backend/
├── main.py                          # Uvicorn entry → re-exports app.main:app
├── app/
│   ├── main.py                      # FastAPI: CORS, lifespan, /api/v1 routers, handlers
│   ├── config.py                    # env, DATABASE_URL, JWT, per-module Anthropic keys
│   ├── database.py                  # async engine, get_db(), create_all_tables
│   ├── dependencies.py              # JWT, X-Family-Member-Id, get_ai_user_context
│   ├── models/                      # SQLAlchemy → PostgreSQL
│   │   ├── user.py
│   │   ├── profile/                 # user profile tables: risk, tax, constraints, other assets
│   │   ├── goals/                   # goals, contributions, holdings
│   │   ├── mf/                      # MF ledger, SIPs, NAV, snapshots, lists
│   │   ├── stocks/                  # equity txns, prices, company metadata
│   │   ├── portfolio.py
│   │   ├── chat.py, chat_ai_module_run.py
│   │   └── …                        # linked_account, family_member, fund, ips, notifications, …
│   ├── schemas/                     # Pydantic (not ORM)
│   │   ├── profile/                 # CompleteProfile API payloads
│   │   ├── ai_modules/              # bodies for /ai-modules agent test routes
│   │   ├── ingest/                  # Finvu / AA payloads
│   │   └── auth.py, chat.py, portfolio.py, goal.py, onboarding.py, …
│   ├── routers/                     # HTTP; mounted under /api/v1 via __init__.py
│   │   ├── health.py, auth.py, onboarding.py, profile.py, goals.py
│   │   ├── portfolio.py             # + Finvu sync route
│   │   ├── chat.py                  # sessions/messages → ChatBrain
│   │   ├── simbanks.py, ips.py, family.py, linked_accounts.py
│   │   ├── discovery.py, meeting_notes.py, notifications.py, rebalancing.py
│   │   └── ai_modules/              # HTTP: /ai-modules (per-agent test routes)
│   │       ├── intent_classifier/, market_commentary/, portfolio_query/
│   │       ├── asset_allocation/, drift_analyzer/, mutual_fund_status/, risk_profile/
│   │       └── __init__.py
│   ├── services/
│   │   ├── chat_core/               # ChatBrain.run_turn (intent → branches)
│   │   ├── ai_bridge/               # intent, market, general chat, portfolio text, allocation wiring
│   │   ├── ai_module_telemetry.py
│   │   ├── chat_context.py, chat_service.py
│   │   ├── user_context.py, user_service.py, goal_service.py
│   │   ├── auth_service.py, otp_service.py, notification_service.py
│   │   ├── portfolio_service.py, finvu_portfolio_sync.py, simbanks_service.py
│   │   └── __init__.py
│   ├── utils/security.py
│   └── data/                        # e.g. dummy_data.json (seed script only)
├── AI_Agents/
│   └── src/                         # prepended to sys.path (allocation, intent, market, …)
│       ├── allocation/, intent_classifier/, market_commentary/
│       ├── portfolio_query/, risk_profiling/
│       └── data/                    # fund_view.txt (+ Reference_files fallback)
├── alembic/                         # env.py, versions/*.py
├── alembic.ini
├── scripts/
│   └── reset_and_seed_dummy_data.py
├── wealth_core/                     # legacy modules; main app lives under app/
├── requirements.txt, Dockerfile
├── README_DATABASE_SCHEMA.md
├── .env.example
└── README.md                        # this file
```

---

## Core wiring

| Module | Responsibility |
|--------|----------------|
| [`app/main.py`](./app/main.py) | App factory, CORS, lifespan (metadata `create_all`, engine dispose), mount `all_routers` at `API_V1_PREFIX`, validation/DB-friendly error handlers. |
| [`app/config.py`](./app/config.py) | `get_settings()`: DB URL (asyncpg, encoding, pgbouncer strip), `JWT_SECRET`, `ENCRYPTION_KEY`, CORS, per-feature Anthropic keys (`INTENT_CLASSIFIER_API_KEY`, `MARKET_COMMENTARY_API_KEY`, `ASSET_ALLOCATION_API_KEY`, `RISK_PROFILING_API_KEY`, `PORTFOLIO_QUERY_API_KEY`) with `ANTHROPIC_API_KEY` as shared fallback. |
| [`app/database.py`](./app/database.py) | `Base`, async engine/session, `get_db()`, `create_all_tables`, `dispose_engine`. |
| [`app/dependencies.py`](./app/dependencies.py) | OAuth2 JWT → `CurrentUser`; optional family header → effective user; `get_ai_user_context` → `User` with relations for AI. |
| [`app/utils/security.py`](./app/utils/security.py) | Password hashing, JWT encode/decode. |

[`app/models/__init__.py`](./app/models/__init__.py) imports all models so `Base.metadata` and Alembic see every table.

---

## Models and schemas (summary)

| Layer | Purpose |
|-------|---------|
| **`app/models/*`** | One ORM class per table (or domain subpackage). Relationships hang off `User`. |
| **`app/schemas/*`** | Request/response models only. `profile/` = CompleteProfile; `ai_modules/` = `/ai-modules` bodies; `ingest/` = Finvu; flat files (`chat`, `portfolio`, …) for stable imports. |

The **directory tree** above lists subfolders; for column-level detail use **README_DATABASE_SCHEMA.md**.

---

## HTTP routers

Registered in [`app/routers/__init__.py`](./app/routers/__init__.py). All listed routes sit under **`/api/v1`** except where noted.

| Router file | Route prefix | Role |
|-------------|--------------|------|
| `health` | `/health` | Health check |
| `auth` | `/auth` | Login, tokens, registration |
| `onboarding` | `/onboarding` | Early profile, other assets, completion flag |
| `profile` | `/profile` | Full CompleteProfile read/update |
| `goals` | `/goals` | Goals, contributions, holdings |
| `portfolio` | `/portfolio` | Primary portfolio, allocations, holdings, history, **Finvu** ingest |
| `chat` | `/chat` | Sessions, messages, uploads; send uses **ChatBrain** |
| `meeting_notes`, `notifications` | | Notes and alerts |
| `discovery` | | Client discovery helpers |
| `rebalancing` | | Rebalancing recommendations |
| `ips` | `/ips` | Investment policy statements |
| `linked_accounts` | | Linked accounts |
| `family` | | Family linking; `X-Family-Member-Id` to act as member |
| `simbanks` | | SimBanks ConnectHub → portfolio + MF |
| `ai_modules` (package) | `/ai-modules` | Agent test routes: intent, market, portfolio query, allocation, drift, MF status, risk |

---

## Services

| Area | Files | Role |
|------|--------|------|
| **Chat** | `chat_core/types.py`, `brain.py` | `ChatTurnInput` / `ChatBrainResult`; **`run_turn`** orchestrates intent → branches → telemetry. |
| | `chat_context.py` | Load session messages as `{role, content}` for LLM. |
| | `chat_service.py` | Thin re-exports of `classify_user_message`, `generate_*`. |
| **AI bridge** | `ai_bridge/common.py` | `ensure_ai_agents_path()`, `build_history_block()`. |
| | `intent_*`, `market_*`, `general_chat`, `portfolio_query`, `asset_allocation`, `ailax_flow`, `liquidity_gate` | Call `AI_Agents` + format replies; allocation maps `User` → orchestrator; spine + liquidity for portfolio-style chat. |
| **Telemetry** | `ai_module_telemetry.py` | `PROZPR_AI_MODULE_RUN` logs + `ChatAiModuleRun` rows; `PROZPR_CHAT_FLOW` + `chat_flow` summary per turn. |
| **Portfolio / ingest** | `portfolio_service.py` | Primary portfolio get/create. |
| | `finvu_portfolio_sync.py` | Bucket totals → `PortfolioAllocation` (Cash/Debt/Equity/Other). |
| | `simbanks_service.py` | HTTP/XML → linked accounts, MF, portfolio tables. |
| **Domain** | `user_context.py` | `load_user_for_ai` (eager graph for chat/AI). |
| | `user_service.py`, `goal_service.py`, `auth_service.py`, `otp_service.py`, `notification_service.py` | Router-facing helpers. |

---

## External data and files

- **`AI_Agents/src`** — Added to `sys.path`; imports like `allocation.orchestrator` resolve here. Treat as upstream logic.
- **`AI_Agents/src/data/fund_view.txt`** — Expected by allocation. If missing, [`asset_allocation_service`](./app/services/ai_bridge/asset_allocation_service.py) copies from `data/Reference_files/fund_view.txt`.
- **Caches / dumps** — e.g. `market_commentary_cache.json`, `macro_snapshot_*.json` at repo root may appear at runtime; not schema source of truth (optional `.gitignore`).
- **`app/data/*.json`** — Dev fixtures for [`scripts/reset_and_seed_dummy_data.py`](./scripts/reset_and_seed_dummy_data.py) only.

---

## Execution flows

**Chat (`ChatBrain.run_turn`)**  
1) Start timer + step list for telemetry.  
2) `classify_user_message` (Anthropic intent key).  
3) Branch: `general_market_query` → optional `generate_market_commentary` (120s cap) + `generate_general_chat_response`; `portfolio_optimisation` / `goal_planning` → portfolio path; `portfolio_query` → `generate_portfolio_query_response` from loaded `User`; else general chat.  
4) Portfolio path: `detect_spine_mode`; cash-out may short-circuit via `liquidity_gate` or run `build_prozpr_spine` → `compute_allocation_result` → `format_allocation_chat_brief`.  
5) On error: keyword fallback or safe message.  
6) `log_chat_turn_flow_summary`; return `ChatBrainResult`.  
Data: Postgres session, `load_user_for_ai`, `load_conversation_history`, Anthropic.

**Allocation spine**  
Ensure fund view file → map `User` to allocation DTOs → `AllocationOrchestrator` → optional block message → `format_allocation_chat_brief`.

**Finvu sync**  
`get_or_create_primary_portfolio` → aggregate buckets → if `total > 0`, replace allocation rows with weighted Cash/Debt/Equity/Other.

**SimBanks (summary)**  
Router → `simbanks_service`: ConnectHub + XML → linked accounts, MF rows, portfolio holdings/allocations/history.

**Typical authenticated call**  
`Authorization: Bearer` → `get_current_user` → `get_effective_user` → handler uses `get_db()` session.

---

## Migrations, scripts, environment

- **Alembic:** [`alembic/env.py`](./alembic/env.py) imports `app.models`; revisions under `alembic/versions/`. Prefer migrations in shared/prod; `create_all` helps local dev.
- **Scripts:** `reset_and_seed_dummy_data.py` — destructive/dev seed from JSON.
- **Env:** `DATABASE_URL`, `JWT_SECRET`, `ENCRYPTION_KEY`, `ALLOWED_ORIGINS`, Anthropic keys (see [`.env.example`](./.env.example)).

---

## Related

- [README_DATABASE_SCHEMA.md](./README_DATABASE_SCHEMA.md) — tables and columns.
- Interactive API: `/docs`, `/redoc` when the server is running.

When you add routers or services, update the **tree** and the **Services** / **HTTP routers** tables so paths stay traceable from HTTP → service → DB / `AI_Agents` / files.
