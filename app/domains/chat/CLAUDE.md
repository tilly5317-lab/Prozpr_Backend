# app/domains/chat/ — chat sessions, messages, per-session state, AI-module run telemetry

Persistence + the HTTP send endpoint only. Orchestration of the chat *turn* lives in `app/domains/ai_engine`.

## Layers

- **models/** — `ChatSession`, `ChatMessage` (+ role/status enums), `ChatSessionState` (cross-turn gates), `ChatAiModuleRun` (per-turn telemetry).
- **schemas/** — send-message, session-detail, AI-module-run payloads.
- **routers/** — `/chat` — sessions CRUD, send (delegates to `ChatBrain.run_turn`), statement upload, module-runs audit.
- **services/** — `chat_context` (loads `conversation_history` for the LLM), `ai_module_telemetry` (records module runs + turn-flow summary), `chat_title_service`.

## Gotchas & invariants

- `chat_title_service.generate_chat_title` is a best-effort Haiku call that names a session from its first turn; on any error it falls back to a deterministic intent+snippet label and never raises, so titling can't block the reply (`services/chat_title_service.py`).

## Don't read

- `__pycache__/`, `services/tests/`.
