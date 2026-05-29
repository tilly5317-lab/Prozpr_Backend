# app/domains/chat/ — chat sessions, messages, per-session state, AI-module run telemetry. Orchestration of the chat *turn* lives in app/domains/ai_engine — this domain only owns the persistence + the HTTP send endpoint

Chat sessions, messages, per-session state, ai-module run telemetry. orchestration of the chat *turn* lives in app/domains/ai_engine — this domain only owns the persistence + the http send endpoint.

## Layers

- **models/** — ChatSession, ChatMessage, ChatMessageRole, ChatSessionStatus, ChatSessionState (cross-turn gates), ChatAiModuleRun (per-turn telemetry rows)
- **schemas/** — send-message, session-detail, AI module run response payloads
- **routers/** — /chat router — sessions CRUD, send (delegates to ChatBrain.run_turn), statement upload, module-runs audit
- **services/** — chat_service, chat_context (loads conversation_history for the LLM), ai_module_telemetry (record_ai_module_run + log_chat_turn_flow_summary)

## Don't read

- `__pycache__/`.
