# app/domains/market_commentary/ — app-layer gateway to the bundled market-commentary agent

Gateway domain over the bundled `AI_Agents` market_commentary agent. Owns no persistence — it drives the agent that writes the macro-commentary cache and hands the doc to the chat brain.

## Entry / contract
- `market_commentary_module_service.run(turn, ctx, prior)` is the brain entry point and the single permitted caller of `generate_market_commentary`; a timeout degrades to an empty payload so the turn continues.

## Layers
- **services/** — two files:
  - `market_commentary_module_service` — the brain-facing gateway (above).
  - `market_commentary_engine` — the AI bridge: resolves cache paths + freshness config, then drives `MarketCommentaryAgent` (cache-fast-path → full-run). The agent itself writes the `Reference_docs/market_commentary_latest.md`/`.json` cache, the contract `portfolio_query` and `general_chat` read (`services/market_commentary_engine.py`).

## Don't read
- `__pycache__/`.
