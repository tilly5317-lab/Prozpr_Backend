# app/routers/ai_modules/ — relocated AI routes (docs only)

The per-agent API routers that lived here (intent classifier, market commentary, portfolio query, risk profile, drift analysis, rebalancing, stubs) were consolidated into `app/domains/ai_engine/routers/` during the DDD restructure — that package still serves the `/api/v1/ai-modules/...` prefix. The asset-allocation route was dropped rather than moved. No router code remains here.

## Files

- `README.md` — legacy guide to the old AI-modules routing layer and its migration into the per-domain structure.

## Don't read

- `__pycache__/`.
