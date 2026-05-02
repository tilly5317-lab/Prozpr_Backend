# app/services/chat_core/ — Chat turn orchestrator

Orchestrates a single chat turn. `ChatBrain.run_turn` takes a `ChatTurnInput`,
classifies intent, routes to the appropriate AI bridge, and returns a
`ChatBrainResult` with the assistant reply and telemetry metadata.

## Files

- `types.py` — `ChatTurnInput`, `ChatBrainResult` Pydantic models (turn input/output DTOs).
- `brain.py` — `ChatBrain.run_turn` orchestrator; owns intent-branch routing and telemetry.
- `turn_context.py` — `AgentRunRecord` plus per-turn context bundle (history + last `ChatAiModuleRun` per module + active intent), built once per turn from `ChatTurnInput` and consumed by routing and downstream handlers.
- `__init__.py` — re-exports `ChatBrain`, `ChatTurnInput`, `ChatBrainResult`.

## Tests

- `tests/` — pytest suites for `ChatBrain` and turn-context plumbing.

## Entry point

- `ChatBrain` (from `brain.py`) — imported by `app/routers/chat.py` for the send endpoint.

## Depends on

- `app/services/ai_bridge/` — all intent branches (intent classification, general chat,
  market commentary, portfolio query, allocation spine).
- `app/services/ai_module_telemetry` — `log_chat_turn_flow_summary` per-turn telemetry rows.

## Flow

**Chat turn** (`ChatBrain.run_turn`)
1. Start timer and step list for telemetry.
2. Classify intent via `classify_user_message`.
3. Branch on intent: `general_market_query` → optional `generate_market_commentary`
   then `generate_general_chat_response`; `asset_allocation` / `goal_planning`
   → portfolio path; `portfolio_query` → `generate_portfolio_query_response` from
   loaded User; else general chat.
4. Portfolio path: `detect_spine_mode`, then `build_ailax_spine` →
   `compute_allocation_result` → `format_allocation_chat_brief`.
5. On error: keyword fallback or safe message.
6. `log_chat_turn_flow_summary`; return `ChatBrainResult`.

## Don't read

- `__pycache__/`.
