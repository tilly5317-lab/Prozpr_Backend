# app/domains/general_chat/ — Anthropic-backed fallback chat (general / market Q&A)

Gateway domain: composes a tailored reply when no specialist module owns the intent. Persists nothing, so it has only a `services/` layer.

## Entry / contract
- `general_chat_module_service.run(turn, ctx, prior)` is the gateway for the chat sequence — the only permitted caller of `generate_general_chat_response`. Invoked by `ai_engine`'s `general_chat` and `general_market_query` flows; reads the macro doc from the prior `market_commentary` output via `prior`. Second entry: on the classifier-only intents (`out_of_scope` / `stock_advice`) the brain short-circuits the sequence and calls `general_chat_engine.format_redirect_or_canned` directly (`ai_engine/services/brain.py`). Invoked by `ai_engine`'s `general_chat` and `general_market_query` flows; reads the macro doc from the prior `market_commentary` output via `prior`.

## Layers
- **services/** — two files:
  - `general_chat_module_service` — the brain-facing gateway (above); shapes the engine result into a `ModuleOutput`.
  - `general_chat_engine` — two-pass Anthropic engine: a `web_search`-enabled research pass, then a schema-forced compose pass. Also owns the classifier-only redirect: `format_redirect_or_canned` resolves the sub-reason line, then for `stock_advice` and out-of-scope OFF_TOPIC/OTHER runs it through the shared answer formatter (`action_mode="redirect"`) so the reply acknowledges what was actually asked; the sensitive sub-reasons (gibberish, identity, credentials, chat-summary) stay verbatim-canned, and a formatter failure falls back to the same canned line (`services/general_chat_engine.py`, `should_tailor`).

## Don't read
- `__pycache__/`, `tests/`.
