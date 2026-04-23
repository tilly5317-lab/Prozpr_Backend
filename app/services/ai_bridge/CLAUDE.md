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
- `asset_allocation_service.py` — maps User to `AllocationInput`; `compute_allocation_result`,
  `format_allocation_chat_brief`.
- `ailax_flow.py` — `detect_spine_mode`, `build_ailax_spine`; portfolio spine orchestration.
- `liquidity_gate.py` — `assess_liquidity_for_cash_out`, `format_quick_cash_out_response`;
  cash-out short-circuit logic.
- `goal_allocation_input_builder.py` — `build_goal_allocation_input_for_user`; maps ORM
  goals to allocation DTOs.
- `ailax_trace.py` — `trace_line`, `trace_response_preview`; debug tracing helpers.
- `__init__.py` — re-exports bridge entry points consumed by `chat_core`.

## Entry point

- Each bridge module exposes a top-level function; all imported by
  `app/services/chat_core/brain.py`.
- `ensure_ai_agents_path()` is called once per process to inject `AI_Agents/src`
  into `sys.path`.

## Depends on

- `AI_Agents/src/*` — each bridge calls into one agent (intent, market commentary,
  portfolio query, allocation).
- `app/services/user_context` — `load_user_for_ai` User graph.
- `app/models/*` — reads ORM User / portfolio / goals data.

## Tests

- Command: `pytest app/services/ai_bridge/tests/ -v`
- Key suites: `test_asset_allocation_service.py`, `test_asset_allocation_formatter.py`,
  `test_goal_allocation_input_builder.py`, `test_allocation_recommendation_persist.py`.

## Flow

**Allocation spine** (`build_ailax_spine` → `compute_allocation_result`)
1. Ensure fund view file is present (copy from Reference_files fallback if needed).
2. Map ORM User to allocation DTOs via `build_goal_allocation_input_for_user`.
3. Invoke `AllocationOrchestrator` with the mapped DTOs.
4. Optional liquidity / block message via `liquidity_gate`.
5. `format_allocation_chat_brief` to produce chat-ready reply.

## Don't read

- `__pycache__/`.
- `tests/` — test fixtures, not source of truth.

## Refresh

If stale, run `/refresh-context` from this folder.
