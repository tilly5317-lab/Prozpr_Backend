# app/ — FastAPI application (domain-first)

FastAPI application package, organised **domain-first**: every business capability owns its `models/` + `schemas/` + `routers/` + `services/` under `app/domains/<family>/`. Cross-cutting infra (config, DB, deps, security, exception handlers) lives in `app/core/`. AI workloads are delegated to `AI_Agents/` via `sys.path` injection.

## Child modules

- **core/** — cross-cutting infra (`config`, `database`, `dependencies`, `security`, `lifespan`, `exceptions`, `observability`). No domain logic. See `core/CLAUDE.md`.
- **domains/** — one folder per business domain (21), each carrying only the sub-folders it actually needs from `models/` + `schemas/` + `routers/` + `services/` (seven are sparser; some also add `tests/` or engine sub-packages):
  - **identity/** — user, auth, OTP, family members, linked accounts, onboarding
  - **profile/** — risk, tax, investment, constraints, personal finance, properties
  - **goals/** — financial goals, contributions, holdings
  - **portfolio/** — portfolio + allocations + holdings + history + NAV history
  - **benchmarks/** — benchmark index data (e.g. Nifty50 TRI), scheduler-fed
  - **mutual_funds/** — MF metadata, NAV, txns, SIPs, snapshots, watchlists, AA imports, mfapi.in
  - **equities/** — company metadata, prices, transactions
  - **asset_allocation/** — allocation runs, buckets, aggregates, targets
  - **rebalancing/** — rebalancing runs, trades, warnings, fund rows, subgroup summaries
  - **additional_investment/** — additional-investment chat/engine (deploy amount + cadence extractor)
  - **cashflow/** — cashflow plan engine: assumptions, one-off events, plan runs, headlines
  - **ingestion/** — CAMS-CAS PDF, SimBanks, Finvu (legacy) ingest adapters
  - **advisory/** — IPS, meeting notes, discovery helpers
  - **notifications/** — notification records and delivery
  - **chat/** — chat sessions, messages, per-session state, AI module run telemetry
  - **ai_engine/** — chat orchestrator (`ChatBrain`) + the `FLOWS` intent→flow table, shared chat kernel, answer formatter, visualizations. Hub for every chat turn. See `ai_engine/CLAUDE.md`.
  - **intent_classifier/** — gateway to `AI_Agents.intent_classifier`; the first module each turn
  - **market_commentary/** — gateway to `AI_Agents.market_commentary`; generates the macro doc consumed downstream
  - **practical_asset_allocation/** — holdings-aware allocation (variant of `asset_allocation`); first step of the rebalancing flow
  - **general_chat/** — Anthropic-backed fallback chat (web-search research + composed reply) when no specialist owns the intent
  - **support/** — in-app issue reports: logs to the Google Sheet register (+ optional screenshot/email)
- **routers/** — thin top-level package: `health.py`, `tags.py` (OpenAPI tags), and the aggregator `__init__.py` exposing `all_routers` for `main.py`. `ai_modules/` holds docs only.
- **services/** — DEAD refactor residue: every subpackage (`ai_bridge/`, `chat_core/`, `effective_risk_profile/`, `mf/`, `visualization_tools/`) is a tests/pycache shell with no production code. Live homes: chat kernel/formatter/visualizations → `domains/ai_engine/`; effective risk → `domains/profile/services/_effective_risk/`; rebalancing input builder → `domains/rebalancing/services/rebal_engine/`. Don't add new code here.

## Files at this level

- `main.py` — app factory, CORS, lifespan (metadata `create_all`, engine dispose), mounts `all_routers` at `API_V1_PREFIX`, registers exception handlers.
- `all_models.py` — imports every domain's ORM models so they register with `Base.metadata` (Alembic migration coverage).
- `data/dummy_data.json` + `data/mf_tables_sample.json` — dev seed fixtures.

## Conventions

- **Layer shape per domain.** Most domains carry all four sub-folders, but sparse ones carry only what they use (`general_chat/` and `market_commentary/` are services-only; `asset_allocation/` has no `routers/` yet) — check before assuming a layer exists. Router files end in `_router.py`; service files end in `_service.py` (helpers like `paging.py` keep their own name).
- **Router aggregator.** `app/routers/__init__.py` is the single place that knows mount order. `main.py` only imports `all_routers`.
- **Auth.** JWT via `core.dependencies.get_current_user`; family-member override via `X-Family-Member-Id` resolved by `get_effective_user`. AI handlers receive a pre-loaded User graph via `get_ai_user_context`.
- **Async everywhere.** `get_db()` yields an `AsyncSession`; all DB calls `await session.execute(...)`.
- **LLM calls go through LangChain** — see root `CLAUDE.md`.

## Flows

Cross-cutting flows live with their home folders:
- Chat turn (`ChatBrain.run_turn`) → `app/domains/ai_engine/CLAUDE.md`.
- CAMS CAS PDF ingest, SimBanks sync → `app/domains/ingestion/CLAUDE.md`.

## Don't read

- `__pycache__/`, `.pytest_cache/`, `.venv/`, `.obsidian/` — build/editor caches.
- `models/`, `schemas/`, `routers/mf/` — pycache-only shells from the domain-first refactor; the live models/schemas are per-domain.
- `*.db`, `*.db.bak-*`, `*.db.partial-*` — local SQLite dev state.
- `market_commentary_*.json`, `market_commentary_*.md` — runtime cache files.

## Refresh

If any CLAUDE.md in this tree looks stale after a structural change, run `/refresh-context` from that folder. Leaf CLAUDE.mds intentionally omit a per-file refresh note — this is the canonical one.
