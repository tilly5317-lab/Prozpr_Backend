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

- `app/services/ai_bridge/` — intent classification, market commentary, portfolio query,
  general chat, plus the per-intent chat handlers dispatched via `chat_dispatcher`
  (`asset_allocation/chat.py`, `goal_planning/chat.py`, `rebalancing/chat.py`).
- `app/services/ai_module_telemetry` — `log_chat_turn_flow_summary` per-turn telemetry rows.

## Flow

**Chat turn** (`ChatBrain.run_turn`)
1. Build `turn_context` (history + last `ChatAiModuleRun` per module + active intent).
2. Classify intent via `classify_user_message` (history-aware via `active_intent`).
3. Branch on intent:
   - `general_market_query` → `generate_market_commentary` (timed out at 120 s ⇒ skip) then
     `generate_general_chat_response`.
   - `asset_allocation` → lazy-import `asset_allocation.chat` (self-`@register`s) then
     `dispatch_chat(intent, turn_context)`; returns text + snapshot/rebalancing IDs.
   - `goal_planning` → lazy-import `goal_planning.chat`, `dispatch_chat`; returns text only.
   - `rebalancing` → lazy-import `rebalancing.chat`, `dispatch_chat`; returns text +
     snapshot/rebalancing IDs.
   - `portfolio_query` → `generate_portfolio_query_response`.
   - else → `generate_general_chat_response` (general-chat fallback).
4. On exception: rollback DB if needed and return a safe `_CLASSIFIER_FAILURE_MESSAGE`.
5. `log_chat_turn_flow_summary` (intent, confidence, steps, duration); return `ChatBrainResult`.

The `_GOAL_PLANNING_SENTINEL` constant exists so the classifier can strip pre-cutover
"goal_planning isn't built yet" canned redirects from historical chat so the LLM doesn't
anchor on them.

## Don't read

- `__pycache__/`.
