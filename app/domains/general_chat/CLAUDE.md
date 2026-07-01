# app/domains/general_chat/ — Anthropic-backed fallback chat (general / market Q&A)

Gateway domain: composes a tailored reply when no specialist module owns the intent. Persists nothing, so it has only a `services/` layer.

## Entry / contract
- `general_chat_module_service.run(turn, ctx, prior)` is the gateway — the only permitted caller of the engine. Invoked by `ai_engine`'s `general_chat` and `general_market_query` flows; reads the macro doc from the prior `market_commentary` output via `prior`.

## Layers
- **services/** — two files:
  - `general_chat_module_service` — the brain-facing gateway (above); shapes the engine result into a `ModuleOutput`.
  - `general_chat_engine` — two-pass Anthropic engine: a `web_search`-enabled research pass, then a schema-forced compose pass. Also returns sub-reason-tailored canned replies for out-of-scope / stock-advice intents (`services/general_chat_engine.py`).

## Don't read
- `__pycache__/`, `tests/`.
