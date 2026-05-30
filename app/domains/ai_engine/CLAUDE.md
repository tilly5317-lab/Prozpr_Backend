# app/domains/ai_engine/ — AI engine domain

The chat orchestrator + intent router + per-intent bridges to `AI_Agents`.
This is the home of `ChatBrain.run_turn` — the one entry point per chat turn.

Public surface:

```python
from app.domains.ai_engine import ChatBrain, ChatTurnInput, ChatBrainResult
```

## Layout

```
ai_engine/
  routers/                          # per-agent debug HTTP routes
  schemas/                          # request/response payloads for those routes
  services/
    common.py                       # shared helpers (sys.path inject, tracing,
                                    # history-block formatting, money formatting)
    chat_dispatcher.py              # @register / dispatch_chat registry for the
                                    # per-module chat handlers
    types.py                        # BranchResult, IntentDecision
    chat_orchestrator/
      brain.py                      # ChatBrain — the per-turn loop
      turn_context.py               # TurnContext + build_turn_context()
      types.py                      # ChatTurnInput, ChatBrainResult DTOs
    intent_router/
      intent_router.py              # _DISPATCH map + dispatch()
      classifier_llm.py             # shared Haiku structured-output helper
    bridges/
      intent_classifier_bridge.py   # routing intent → IntentDecision
      intent_classifier_service.py  # actual classify_user_message() (LangChain)
      general_chat_bridge.py
      general_chat_service.py
      market_commentary_bridge.py
      market_commentary_service.py
      portfolio_query_bridge.py
      portfolio_query_service.py
      asset_allocation_bridge.py    # thin run() wrapper
      asset_allocation/             # implementation (chat, service, input_builder,
                                    # overrides, allocation_tables_md, persistence)
      goal_planning_bridge.py
      goal_planning/                # implementation
      rebalancing_bridge.py
      rebalancing/                  # implementation
      ailax_flow.py                 # legacy allocation-spine helper
    answer_formatter/
      formatter.py                  # shared question-aware answer formatter
    visualizations/
      registry.py
      category_gap_bar/builder.py
```

## Concepts

- **`TurnContext`** (`chat_orchestrator/turn_context.py`) — per-turn bag:
  history + last `ChatAiModuleRun` per module + active intent + `awaiting_save`
  gate. Built once per turn by `build_turn_context`; bridges read from it.
- **`IntentDecision`** (`services/types.py`) — routing key (`.name`) +
  confidence + reasoning + the raw `IntentClassification` pydantic on `.raw`
  (so bridges that need the full classifier output — `general_chat`,
  `market_commentary` — don't have to classify again).
- **`BranchResult`** (`services/types.py`) — every bridge returns this shape:
  text + optional persisted IDs (allocation / rebalancing / snapshot) +
  optional chart payloads + a `side_effects` bag reserved for cross-turn
  gates (e.g. `awaiting_save`).

## The brain loop (`ChatBrain.run_turn`)

1. `build_turn_context(turn)` — history + last AgentRun per module + active
   intent + `awaiting_save`.
2. `intent_classifier_bridge.classify_for_turn(turn, ctx)` → `IntentDecision`.
3. `intent_router.dispatch(ctx, intent, turn=…, flow=…)` — runs the matching
   bridge under a 60-second hard timeout. Returns a `BranchResult`.
4. `finalize(branch)` — writes per-turn telemetry under a 5-second cap
   (best-effort; reply ships even if telemetry hangs) and shapes
   `ChatBrainResult` for the HTTP layer.

The orchestrator owns ONLY the timeout / error / telemetry envelope. All agent
shape concerns live in the bridges.

## The dispatch switch (`intent_router._DISPATCH`)

Single source of truth: `intent.name` → bridge `run` coroutine.

```python
_DISPATCH = {
    "general_market_query": market_commentary_bridge.run,
    "asset_allocation":     asset_allocation_bridge.run,
    "goal_planning":        goal_planning_bridge.run,
    "rebalancing":          rebalancing_bridge.run,
    "portfolio_query":      portfolio_query_bridge.run,
    "general_chat":         general_chat_bridge.run,
}
```

Adding a new intent is two edits: a new bridge file + one row here. No edits
in the brain.

Cross-turn gate: when `ctx.awaiting_save` is true, dispatch bypasses the
table and routes to `goal_planning_bridge.run` so the user's
"yes save / no discard" answer reaches the right handler.

## Bridges — same shape every time

```python
async def run(ctx, *, turn, flow, intent) -> BranchResult: ...
```

- `ctx` — `TurnContext`
- `turn` — original `ChatTurnInput`
- `flow` — `list[str]`, appended to for telemetry
- `intent` — `IntentDecision`; bridges that want the full classifier shape
  read `intent.raw`

The bridge files import from specific service modules
(`bridges/general_chat_service.py`, `bridges/market_commentary_service.py`,
etc.) — never from the `bridges/__init__.py` (which is deliberately empty to
avoid a cycle with those service modules). Each chat handler self-registers
via `@register` decorators in
`asset_allocation/chat.py` / `goal_planning/chat.py` / `rebalancing/chat.py`;
the bridge lazy-imports the matching `chat.py` so the side-effect lands
before `dispatch_chat` runs.

## Depends on

- `AI_Agents/src/*` — the orchestrator modules each bridge talks to
  (intent_classifier, market_commentary, portfolio_query,
  asset_allocation_pydantic, Rebalancing, cashflow_statement).
- `app.domains.chat.services.ai_module_telemetry` —
  `log_chat_turn_flow_summary` per-turn rows.
- Each AI-driven domain's persistence service:
  - `app.domains.asset_allocation.services.allocation_persist_service`
  - `app.domains.rebalancing.services.rebalancing_persist_service`
  - `app.domains.cashflow.services.cashflow_persist_service`

## Tests

- `pytest app/domains/ai_engine -v` covers the shared `services/tests/`
  suite plus each bridge package's co-located `tests/` folder.

## Don't read

- `__pycache__/`.
- `tests/` directories — test fixtures, not source of truth.
