# AI_Agents/Reference_docs

Canonical home for reference documents consumed by AI-module pipelines (skill-prompt sources, market-commentary cache, fund-house outlooks, etc.). Files here are read at runtime by agents under `AI_Agents/src/`.

## Files

- `market_commentary_latest.md` — daily-refreshed Indian macro commentary. Written by `app/services/ai_bridge/market_commentary_service.py` (which drives `AI_Agents/src/market_commentary/main.MarketCommentaryAgent`). Read by `AI_Agents/src/portfolio_query/` for the "Fund House Market Commentary" context block.
- `market_commentary_latest.json` — `MacroSnapshot` cache backing the `.md` (1-hour cache TTL via `MARKET_COMMENTARY_CACHE_MAX_AGE_SEC`).

## Conventions

- Treat files here as **runtime data**, not committed source. Agents may overwrite them on a schedule.
- Add a new reference doc only when at least one AI module needs it as input.

## Don't read

- `*.json`, `*.md` cached artifacts when reviewing for code changes — they're outputs, not source.

## Refresh

If this file looks stale after adding a new reference doc, run `/refresh-context` from this folder.
