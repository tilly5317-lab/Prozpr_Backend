# app/domains/general_chat/ — Anthropic-backed fallback chat (general / market Q&A)

Gateway domain: composes a tailored reply when no specialist module owns the intent. Persists nothing, so it has only a `services/` layer.

## Entry / contract
- `general_chat_module_service.run(turn, ctx, prior)` is the gateway for the chat sequence — the only permitted caller of `generate_general_chat_response`. Invoked by `ai_engine`'s `general_chat` and `general_market_query` flows; reads the macro doc from the prior `market_commentary` output via `prior`. Second entry: on the classifier-only intents (`out_of_scope` / `stock_advice`) the brain short-circuits the sequence and calls `general_chat_engine.format_redirect_or_canned` directly (`ai_engine/services/brain.py`).

## Layers
- **services/** — two files:
  - `general_chat_module_service` — the brain-facing gateway (above); shapes the engine result into a `ModuleOutput`.
  - `general_chat_engine` — two passes: a `web_search`-enabled research pass it owns, then the shared answer formatter, which composes the reply from the research digest (`action_mode="narrate"`). The digest — not the ~7K-char market commentary — is what the compose call sees, so the commentary is sent once per turn, not twice. Also owns the classifier-only redirect: `format_redirect_or_canned` resolves the sub-reason line, then for `stock_advice` and out-of-scope OFF_TOPIC/OTHER runs it through the shared answer formatter (`action_mode="redirect"`) so the reply acknowledges what was actually asked; the sensitive sub-reasons (gibberish, identity, credentials, chat-summary) stay verbatim-canned, and a formatter failure falls back to the same canned line (`services/general_chat_engine.py`, `should_tailor`).

## Gotchas & invariants
- **`generate_general_chat_response` needs `ctx`** — the compose pass runs through the shared formatter, which records telemetry against the turn. The early out-of-scope / stock-advice guards answer without it (`services/general_chat_engine.py`).
- **The research digest is untrusted input.** It is assembled from open-web results, so the body prompt names it alongside `CUSTOMER_QUESTION` as data-never-instructions. Citation tags are stripped before the formatter ever sees it.
- **Justification bullets are prose the model writes, not a tool field.** They used to be a separate `justification_bullets` array rendered in Python; a second prose field competes with `answer` (see `ai_engine/CLAUDE.md`), so the body asks for them inline — only when the question has an actionable implication.

## Don't read
- `__pycache__/`, `tests/`.
