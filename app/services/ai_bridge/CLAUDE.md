# app/services/ai_bridge/ — AI agent adapters

Adapters between `ChatBrain` and the `AI_Agents` orchestrators. One file per
intent branch; each maps ORM User + inputs to an agent's DTO, calls the agent,
and formats the reply for chat.

## Files

- `common.py` — `ensure_ai_agents_path()`, `build_history_block()` shared helpers.
- `intent_classifier_service.py` — `classify_user_message`; wraps AI_Agents intent classifier.
- `market_commentary_service.py` — `generate_market_commentary`; wraps market commentary agent.
- `general_chat_service.py` — `generate_general_chat_response`; general-chat reply generator.
- `portfolio_query_service.py` — `generate_portfolio_query_response`; wraps portfolio query agent.
- `chat_dispatcher.py` — per-intent chat handler registry (`@register`, `dispatch_chat`).
- `ailax_flow.py` — `detect_spine_mode`, `build_ailax_spine`; portfolio spine orchestration.
- `ailax_trace.py` — `trace_line`, `trace_response_preview`; debug tracing helpers.
- `__init__.py` — re-exports bridge entry points consumed by `chat_core`.

## Child packages

- **asset_allocation/** — allocation domain: engine adapter (`service.py`),
  unified chat handler (`chat.py`), input builder (`input_builder.py`),
  and `tests/` co-located with the package. Note: `chat.py` is *not*
  auto-imported by `asset_allocation/__init__.py` (would cycle through
  `chat_core.turn_context`); callers needing its `@register` side-effect
  must import it lazily.

## Entry point

- Each bridge module exposes a top-level function; all imported by
  `app/services/chat_core/brain.py`.
- `ensure_ai_agents_path()` is called once per process to inject `AI_Agents/src`
  into `sys.path`.

## Depends on

- `AI_Agents/src/*` — each bridge calls into one agent (intent, market commentary,
  portfolio query, allocation).
- `app/models/*` — reads ORM User / portfolio / goals data.

## Tests

- Command: `pytest app/services/ai_bridge/ -v` (covers both the shared
  `tests/` folder and `asset_allocation/tests/`).
- Domain-specific suites live in `asset_allocation/tests/`:
  `test_chat.py`, `test_input_builder.py`, `test_service_persists_agent_run.py`.
- Shared suites in `tests/`: `test_chat_dispatcher.py`,
  `test_classifier_service_active_intent.py`.

## Flow

**Allocation spine** (`build_ailax_spine` → `compute_allocation_result`)
1. Ensure fund view file is present (copy from Reference_files fallback if needed).
2. Map ORM User to allocation DTOs via `asset_allocation.input_builder.build_goal_allocation_input_for_user`.
3. Invoke `AllocationOrchestrator` with the mapped DTOs.
4. `asset_allocation.service.format_allocation_chat_brief` to produce chat-ready reply.

## Don't read

- `__pycache__/`.
- `tests/` — test fixtures, not source of truth.

## Refresh

If stale, run `/refresh-context` from this folder.
