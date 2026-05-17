# app/services/ai_bridge/goal_planning/ — Goal-planning bridge

Bridges `AI_Agents/src/cashflow_statement` (engine + LangGraph agent) into
chat. Wraps `run_cashflow_statement`, then projects the resulting snapshot
into a curated `facts_pack` dict that the shared answer-formatter LLM turns
into the customer-facing reply.

## Files

- `service.py` — runs the cashflow agent for the user's input, then builds the `facts_pack` (headline status, lever options, narrative) consumed by the formatter.
- `chat.py` — chat handler for the `goal_planning` intent. Calls the input builder + service, hands `facts_pack` to the shared formatter. This module never templates user-visible strings — the formatter LLM owns voice. Same lazy-import contract as `asset_allocation/chat.py`: self-registers via `@register`; `brain.py` imports it on the `goal_planning` branch only.
- `input_builder.py` — `build_goal_planning_input_for_user`: maps a User ORM row to `cashflow_statement.GoalPlanningInput`. Known ORM coverage gaps (e.g. `retirement.assumed_lifespan_years`, full `current_properties` rows) fall back to engine defaults; gaps are documented inline.
- `__init__.py` — package marker.
- `tests/` — pytest suite for the bridge.

## Depends on

- `AI_Agents/src/cashflow_statement` — engine + agent + summarizer.
- `app/services/ai_bridge/answer_formatter` — chat-side reply formatting.
- `app/models/*` — User graph (profile, goals, properties, retirement).

## Don't read

- `__pycache__/`.
- `tests/` — fixtures, not source of truth.
