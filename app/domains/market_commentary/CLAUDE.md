# app/domains/market_commentary/ — app-layer gateway to the bundled market-commentary agent

Gateway domain over the bundled `AI_Agents` market_commentary agent. Owns no persistence — it drives the agent that writes the macro-commentary cache and hands the doc to the chat brain.

## Entry / contract
- `market_commentary_module_service.run(turn, ctx, prior)` is the brain entry point and the only permitted caller of `generate_market_commentary` on the chat path; a 120s timeout — or any agent exception — degrades to an empty payload so the turn continues into `general_chat` without a macro doc. `ai_engine`'s debug router also calls the engine directly (`POST /api/v1/ai-modules/market-commentary/generate`), with no timeout and no degradation.

## Layers
- **services/** — three files:
  - `market_commentary_module_service` — the brain-facing gateway (above).
  - `market_commentary_engine` — the AI bridge: resolves cache paths + freshness config, then drives `MarketCommentaryAgent` (cache-fast-path → full-run). The agent itself writes the `Reference_docs/market_commentary_latest.md`/`.json` cache, the contract `portfolio_query` and `general_chat` read (`services/market_commentary_engine.py`).
  - `fund_house_view_module_service` — sibling gateway for the **fund-house view**: a plain read of the hand-maintained `Reference_docs/fund_house_commentry.md` (no agent, no cache), returning `None` when absent. Loaded by `flow_market` when `tools_needed` asks for `fund_house_view`; the reply surfaces the named houses as research sources.

## Don't read
- `__pycache__/`.
