# app/services/ — Business logic

Services hold the non-HTTP, non-ORM business logic: chat orchestration, AI
bridges, domain helpers (portfolio, user, goals, auth, notifications,
telemetry), and ingest adapters (CAMS CAS PDF upload, SimBanks; Finvu is
sidelined — see below).

## Child modules

- **chat_core/** — chat-turn orchestration; `ChatBrain.run_turn` is the entry
  point that drives intent → branch → telemetry.
- **ai_bridge/** — adapters between ChatBrain and AI_Agents orchestrators; one
  file per intent branch (intent, market, portfolio query, allocation, spine,
  general chat) plus child packages for asset_allocation, rebalancing, and
  the shared answer_formatter.
- **effective_risk_profile/** — persistence and calculation helpers for the
  user's effective risk assessment; distinct from deterministic scoring in
  `risk_profiling`.
- **mf/** — mutual-fund domain services: AA import/access, fund metadata,
  investor-detail lookup, mfapi.in fetch/ingest/scheduler, and NAV history
  helpers used by ingest routers and chat.
- **visualization_tools/** — chart-spec builders consumed by chat bridges
  (donuts, gap bars, target-vs-actual, concentration risk, etc.); each
  chart family is its own subpackage and registered via `registry.py`.
- **tests/** — pytest suites for service-layer helpers (telemetry,
  allocation persistence, etc.).
- **archive/** — retired service code kept for reference; not imported by
  active paths.

## Cross-module edges

- `chat_core/` imports from `ai_bridge/` for every intent branch.
- `chat_core/` and `ai_bridge/` both receive a User graph eager-loaded upstream by `user_context.load_user_for_ai` (invoked via `app/dependencies.get_ai_user_context`); they do not import user_context directly.

## Files at this level

- `user_context.py` — `load_user_for_ai`: eager-loads the full User graph for
  chat/AI branches.
- `user_service.py` — router-facing user helpers.
- `goal_service.py` — goals CRUD and computation.
- `auth_service.py` — authentication flows.
- `otp_service.py` — OTP issuance and verification.
- `notification_service.py` — notification creation and delivery.
- `portfolio_service.py` — primary portfolio get/create.
- `cams_cas_ingest.py` — parse an uploaded CAMS/KFintech Consolidated Account
  Statement (CAS) PDF (via the `casparser` package) → `mf_aa_imports` /
  `mf_aa_summaries` / `mf_aa_transactions` raw rows → `MfTransaction` (through
  `mf_aa_normalizer`) → primary-portfolio bucket allocations + one
  `portfolio_holdings` row per scheme. Asset class is resolved by
  `_resolve_asset_bucket` (trust `casparser`'s `type`; else infer from the scheme
  name; else "Other") so funds `casparser` can't classify don't all land in
  "Other". Also back-fills blank identity fields on the `users` row
  (`first_name`/`middle_name`/`last_name`/`email`/`address`/`pan`) from the CAS
  investor block — never overwrites what the user already set. Replaces the
  Finvu fetch-by-mobile flow.
- `finvu_portfolio_sync.py` — DEPRECATED / SIDELINED. Finvu account-aggregator
  ingestion → PortfolioAllocation rows. Paused for licensing; kept for
  reference, not on an active path. Use `cams_cas_ingest.py` instead.
- `simbanks_service.py` — SimBanks ConnectHub XML → linked accounts, MF,
  portfolio tables.
- `ai_module_telemetry.py` — `ChatAiModuleRun` telemetry rows; chat-flow
  summary per turn.
- `chat_context.py` — loads session messages as `{role, content}` for LLM.
- `chat_service.py` — thin re-exports of classify/generate helpers.
- `mf_aa_normalizer.py` — normalises MF Account-Aggregator imports into
  `MfTransaction` rows with dedup fingerprints.
- `allocation_recommendation_persist.py` — persists goal-based allocation
  outputs for rebalancing UI and portfolio snapshots.
- `rebalancing_recommendation_persist.py` — persists a rebalancing-engine
  response as a `RebalancingRecommendation` (REBALANCING_TRADES) row.

## Watch out for

- `simbanks_service.py` is large (~1100 lines). Grep for handler function
  names rather than reading top-to-bottom.
- `chat_service.py` is a thin re-export shim over `ai_bridge/`. Refactoring
  what it exports usually means changing the underlying bridge module too.

## Flows

**CAMS CAS PDF ingest** (`POST /api/v1/mf-ingest/cams-pdf` → `cams_cas_ingest.ingest_cams_pdf`)
1. Router receives a multipart upload (`file` + `password`); reads bytes.
2. `casparser` parses the PDF (run in a thread) → folios → schemes → transactions.
3. Persist raw audit rows: one `MfAaImport` (investor identity, statement
   period), `MfAaSummary` per scheme, `MfAaTransaction` per unit-moving txn;
   commit so a later failure leaves a retry-able RECEIVED row.
4. `mf_aa_normalizer.normalize_single_import` → upserts `MfFundMetadata` and
   inserts deduped `MfTransaction` rows.
5. Roll up the CAS valuations into Cash / Debt / Equity / Other and replace the
   primary portfolio's `PortfolioAllocation` rows (same shape as SimBanks).
6. `maybe_recalculate_effective_risk` (in the router), then commit.

**Finvu sync** — DEPRECATED / SIDELINED (licensing). `finvu_portfolio_sync.apply_finvu_bucket_snapshot`
still backs the legacy `POST /portfolio/finvu/sync` route but is not used by the app.

**SimBanks** (owner per spec §5.1)
1. Router receives a SimBanks ConnectHub sync request.
2. `simbanks_service` fetches the XML payload from ConnectHub.
3. Writes linked-account rows, MF rows, and portfolio holdings / allocations /
   history.

## Don't read

- `__pycache__/`.
- `archive/` — retired; not on active import paths.
