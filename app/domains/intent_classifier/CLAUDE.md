# app/domains/intent_classifier/ — app-layer gateway to the bundled intent classifier

Gateway domain: classifies each chat message into an intent. The ONLY place permitted to touch the bundled `AI_Agents` intent_classifier agent — no persistence, schemas, or router.

## Entry / contract
- `intent_classifier_service.run(turn, ctx, prior)` runs first every turn; `ai_engine`'s brain uses its result to pick the rest of the module sequence (`services/`).

## Layers
- **services/** — two files:
  - `intent_classifier_service` — the uniform module-service surface (above). Returns a `ModuleOutput` whose `payload` is an `IntentDecision` (types owned by `ai_engine`). Never last in a flow, so it produces no user-visible `text`.
  - `intent_classifier_engine` — the bridge to the bundled agent (loaded via `ai_engine.common.ensure_ai_agents_path`). Anthropic primary + OpenAI fallback; scrubs canned-redirect turns from history and applies a rebalancing keyword override. Entry `classify_user_message`, also called by `ai_engine`'s debug intent router.

## Don't read
- `__pycache__/`.
