# app/schemas/ai_modules/

Request/response Pydantic bodies for the `/ai-modules` agent test routes. Each file corresponds to
one agent domain exposed under `app/routers/ai_modules/`.

## Files

- `asset_allocation.py` — `AssetAllocationRequest`, `AssetAllocationResponse`
- `conversation.py` — `ConversationTurn` (shared turn model used by intent classifier)
- `intent_classifier.py` — `IntentClassifyRequest`, `IntentClassifyResponse`
- `market_commentary.py` — `MarketCommentaryResponse`
- `portfolio_query.py` — `PortfolioQueryRequest`, `PortfolioQueryResponse`
- `status.py` — `AIModuleStatusResponse` (generic agent availability response)

## Data contract

- `AssetAllocationRequest` → `AssetAllocationResponse` — consumed by `app/routers/ai_modules/asset_allocation.py`.
- `IntentClassifyRequest` → `IntentClassifyResponse` — consumed by `app/routers/ai_modules/intent_classifier.py`.
- `MarketCommentaryResponse` — consumed by `app/routers/ai_modules/market_commentary.py`.
- `PortfolioQueryRequest` → `PortfolioQueryResponse` — consumed by `app/routers/ai_modules/portfolio_query.py`.
- `AIModuleStatusResponse` — consumed by `app/routers/ai_modules/drift_analyzer.py`,
  `app/routers/ai_modules/risk_profile.py`, and `app/routers/ai_modules/mutual_fund_status.py`.

## Depends on

- `AI_Agents/src/*/models` — agent input/output types that these schemas mirror at the HTTP boundary.

## Don't read

- `__pycache__/`.

## Refresh

If stale, run `/refresh-context` from this folder.
