# app/services/ai_bridge/asset_allocation/ — Allocation bridge

Bridges `AI_Agents/src/asset_allocation_pydantic` into chat. Builds the
allocation input from a User ORM row, runs the pipeline on a worker thread,
optionally persists the output, and renders a chat-ready brief.

## Files

- `service.py` — orchestration: input building, API-key resolution, async thread offload, step-by-step tracing, optional DB persistence, markdown formatting.
- `chat.py` — chat handler for the `asset_allocation` intent. First turn runs the engine and returns a chat brief; subsequent turns reuse the persisted `AgentRun`. **Not** auto-imported from `__init__.py` (would cycle through `chat_core.turn_context`); callers needing its `@register` side-effect must import it lazily.
- `input_builder.py` — `build_goal_allocation_input_for_user`: maps a User ORM row to `AllocationInput`. Reads persisted DB rows only — never calls into `risk_profiling.scoring`; falls back to score 7.0 when an `effective_risk_assessments` row is absent.
- `overrides.py` — `with_chat_overrides`: per-turn chat override helper (replaces the legacy `User._chat_*_override` monkey-patch). Leaf module — neither `chat.py` nor `input_builder.py` imports the other, but both import from here.
- `__init__.py` — package marker (deliberately does not import `chat.py`).
- `tests/` — pytest suite for the bridge.

## Depends on

- `AI_Agents/src/asset_allocation_pydantic` — the allocation pipeline.
- `app/services/ai_bridge/answer_formatter` — chat-side reply formatting.
- `app/models/*` — User graph + persisted allocation/risk rows.

## Don't read

- `__pycache__/`.
- `tests/` — fixtures, not source of truth.
