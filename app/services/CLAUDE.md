# app/services/ — Business logic

Services hold the non-HTTP, non-ORM business logic: chat orchestration, AI
bridges, domain helpers (portfolio, user, goals, auth, notifications,
telemetry), and ingest adapters (Finvu, SimBanks).

## Child modules

- **chat_core/** — chat-turn orchestration; `ChatBrain.run_turn` is the entry
  point that drives intent → branch → telemetry.
- **chat_brain/** — standalone mirror of `chat_core.ChatBrain`; not imported by the live
  chat router — keep behavior in sync when touching portfolio/allocation flow.
- **ai_bridge/** — adapters between ChatBrain and AI_Agents orchestrators; one
  file per intent branch (intent, market, portfolio query, allocation, spine,
  liquidity gate, general chat).
- **effective_risk_profile/** — persistence and calculation helpers for the
  user's effective risk assessment; distinct from deterministic scoring in
  `risk_profiling`.
- **archive/** — retired service code kept for reference; not imported by
  active paths.

## Cross-module edges

- `chat_core/` imports from `ai_bridge/` for every intent branch.
- `chat_core/` and `ai_bridge/` both use `user_context.load_user_for_ai`.

## Files at this level

- `user_context.py` — `load_user_for_ai`: eager-loads the full User graph for
  chat/AI branches.
- `user_service.py` — router-facing user helpers.
- `goal_service.py` — goals CRUD and computation.
- `auth_service.py` — authentication flows.
- `otp_service.py` — OTP issuance and verification.
- `notification_service.py` — notification creation and delivery.
- `portfolio_service.py` — primary portfolio get/create.
- `finvu_portfolio_sync.py` — Finvu ingestion → PortfolioAllocation rows.
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
- `__init__.py`.

## Flows

**Finvu sync** (owner per spec §5.1)
1. `get_or_create_primary_portfolio(user)` ensures a target portfolio row.
2. Aggregate Finvu buckets into bucket-level totals.
3. If `total > 0`, replace the portfolio's allocation rows with weighted
   Cash / Debt / Equity / Other.

**SimBanks** (owner per spec §5.1)
1. Router receives a SimBanks ConnectHub sync request.
2. `simbanks_service` fetches the XML payload from ConnectHub.
3. Writes linked-account rows, MF rows, and portfolio holdings / allocations /
   history.

## Don't read

- `__pycache__/`.
- `archive/` — retired; not on active import paths.

## Refresh

If this file looks stale after a structural change, run `/refresh-context`
from this folder.
