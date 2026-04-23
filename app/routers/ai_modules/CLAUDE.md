# app/routers/ai_modules/ — AI agent test routes

HTTP endpoints for exercising AI_Agents orchestrators directly, bypassing chat. Each module file defines a router for one agent domain. Routes are mounted under `/api/v1/ai-modules` via the parent `routers/__init__.py`.

## Files

- `intent_classifier.py` — classify customer questions into intent categories.
- `market_commentary.py` — generate market commentary documents.
- `portfolio_query.py` — answer portfolio questions with guardrails.
- `asset_allocation.py` — compute ideal asset allocations.
- `drift_analyzer.py` — compute drift between actual and ideal holdings.
- `mutual_fund_status.py` — return mutual fund status info.
- `risk_profile.py` — compute deterministic risk scores and summaries.
- `__init__.py` — assembles sub-routers.

## Endpoints

- `POST /api/v1/ai-modules/intent-classifier/classify` — classify a message into intent.
- `POST /api/v1/ai-modules/market-commentary/generate` — generate market commentary.
- `POST /api/v1/ai-modules/portfolio-query/answer` — answer portfolio question.
- `POST /api/v1/ai-modules/asset-allocation/recommend` — recommend asset allocation.
- `GET /api/v1/ai-modules/drift-analyzer/status` — drift analyzer status (stub).
- `GET /api/v1/ai-modules/mutual-fund-status/status` — mutual fund status info (stub).
- `GET /api/v1/ai-modules/risk-profile/status` — risk profile status (stub).

## Depends on

- `app.schemas.ai_modules` — request/response Pydantic bodies.
- `app.services.ai_bridge.*` — orchestrator bridges for the four live routes: intent, market, portfolio-query, and allocation (drift, MF-status, and risk are stubs).
- `app.dependencies` — `get_ai_user_context`, `get_effective_user`.

## Don't read

- `__pycache__/`.

## Refresh

If stale, run `/refresh-context` from this folder.
