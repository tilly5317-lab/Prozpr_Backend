# app/domains/chat/ — chat sessions, messages, per-session state, AI-module run telemetry

Persistence + the HTTP send endpoint only. Orchestration of the chat *turn* lives in `app/domains/ai_engine`.

## Layers

- **models/** — `ChatSession`, `ChatMessage` (+ role/status enums), `ChatSessionState` (cross-turn gates), `ChatAiModuleRun` (per-turn telemetry).
- **schemas/** — send-message, session-detail, AI-module-run payloads.
- **routers/** — `/chat` — sessions CRUD, send (delegates to `ChatBrain.run_turn`), the SSE streaming twin of send, statement upload, module-runs audit.
- **services/** — `chat_context` (loads `conversation_history` for the LLM), `ai_module_telemetry` (records module runs + turn-flow summary), `chat_title_service`.

## Gotchas & invariants

- **Two send endpoints, one turn.** `POST /sessions/{id}/messages` returns the finished reply; `.../messages/stream` runs the identical `ChatBrain.run_turn` inside `open_token_stream()` and emits SSE `delta` events, then exactly one terminal `done` or `error`. It carries NO `thinking` event — those lines are polled separately from `GET /sessions/{id}/thinking`. **`done` is authoritative** — deltas are provisional, because the formatter discards a truncated response for a deterministic brief and general_chat re-renders its answer, so a client that painted deltas must replace them with `done.assistant_message.content` (`routers/chat_router.py` `send_message_streaming`).
- **`proxy_buffering off` at nginx is REQUIRED, and `X-Accel-Buffering: no` is not a substitute.** Both are set, but production streamed nothing until the nginx directive was added by hand on 2026-08-08. Proof: 20 SSE ticks sent 250ms apart all reached the client in the same instant, 6.25s in; with the directive they arrived paced, at a flat 1.39s lag. nginx releases nothing until a buffer fills and `proxy_buffers` totals 32–64k, which a whole chat answer fits inside — so payload padding cannot rescue this, and a rebuilt host silently loses streaming again until the vhost is fixed. Localhost talks straight to uvicorn and never reproduces it.
- **Streaming needs `proxy_buffering off` at nginx and a single uvicorn worker.** The token sink is in process memory, so the streaming request and the turn must share a worker. Production is pm2, NOT the Dockerfile — `ecosystem.config.cjs` pins `instances: 1` + `exec_mode: "fork"`; raising that breaks streaming silently (`app/domains/ai_engine/streaming.py`).
- **Incremental delivery also depends on a pinned dependency.** The answer is a forced tool call, so it only streams with the `fine-grained-tool-streaming` beta, which needs `langchain-anthropic>=1.4.8` for `ChatAnthropic(betas=[...])` to be a real field. Older releases silently swallow it and the whole answer lands in one burst at the end — no error, just no streaming (`requirements.txt`).
- **`run_turn` does NOT commit — the router does.** Any in-process driver (eval replays, `run_live_turn`) therefore leaves zero engine-run and telemetry rows behind: it all rolls back when the session context exits.

- **A turn, not a message, is the unit of history.** `load_conversation_history` groups rows by shared `created_at` (both rows are written in one transaction). A turn missing its assistant row KEEPS the customer's question and gets `FAILED_TURN_MARKER` in the assistant slot — dropping the question loses the antecedent for a follow-up like "try again", but a lone user message reads to an LLM as a live unanswered question, which sent the portfolio agent down its goal-planning guardrail on 2026-07-25. Assistant-only turns (the brain's apology, nothing the customer said) are dropped outright (`services/chat_context.py`, `FAILED_TURN_MARKER`).

## Don't read

- `__pycache__/`, `tests/`, `services/tests/`.
