# app/domains/chat/ — chat sessions, messages, per-session state, AI-module run telemetry

Persistence + the HTTP send endpoint only. Orchestration of the chat *turn* lives in `app/domains/ai_engine`.

## Layers

- **models/** — `ChatSession`, `ChatMessage` (+ role/status enums), `ChatSessionState` (cross-turn gates), `ChatAiModuleRun` (per-turn telemetry).
- **schemas/** — send-message, session-detail, AI-module-run payloads.
- **routers/** — `/chat` — sessions CRUD, send (delegates to `ChatBrain.run_turn`), statement upload, module-runs audit.
- **services/** — `chat_context` (loads `conversation_history` for the LLM), `ai_module_telemetry` (records module runs + turn-flow summary), `chat_title_service`.

## Gotchas & invariants

- **A turn, not a message, is the unit of history.** `load_conversation_history` groups rows by shared `created_at` (both rows are written in one transaction). A turn missing its assistant row KEEPS the customer's question and gets `FAILED_TURN_MARKER` in the assistant slot — dropping the question loses the antecedent for a follow-up like "try again", but a lone user message reads to an LLM as a live unanswered question, which sent the portfolio agent down its goal-planning guardrail on 2026-07-25. Assistant-only turns (the brain's apology, nothing the customer said) are dropped outright (`services/chat_context.py`, `FAILED_TURN_MARKER`).

## Don't read

- `__pycache__/`, `services/tests/`.
