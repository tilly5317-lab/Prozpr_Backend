# app/domains/cashflow/services/goal_planning_engine/ — chat bridge to the cashflow_statement engine

ORM `User` → `GoalPlanningInput` → `cashflow_statement` engine → `facts_pack` → shared answer-formatter. Reached only through the domain's `cashflow_module_service`.

## Files
- `chat.py` — `@register("goal_planning")` handler: runs the engine, maps refusal errors to user-facing prompts, formats the reply + chart payloads.
- `service.py` — `compute_goal_planning_snapshot`: runs `compute_full_projection`, persists the plan run, builds `facts_pack` + deterministic `fallback_text`.
- `input_builder.py` — `build_goal_planning_input_for_user`: maps profile + properties + active goals to `GoalPlanningInput`.
- `readiness.py` — `evaluate_cashflow_readiness` + `REQUIRED_CASHFLOW_FIELDS`; backs `GET /cashflow/readiness` and the builder's gate.
- `tests/` — pytest suite (chat, input_builder, service).

## Gotchas & invariants
- **Incomplete profiles are HARD-REFUSED, never zero-filled.** The builder evaluates readiness first and raises `missing_required_inputs:<keys>` (or `missing_date_of_birth`) so the engine only runs on real numbers — no placeholder defaults (`input_builder.py`).
- **The builder is synchronous.** It returns `(GoalPlanningInput, debug)` directly; callers must not await it — only the engine run is offloaded via `asyncio.to_thread` (`service.py`).
- **`chat.py` never templates user-visible prose for success.** It builds `facts_pack` and lets the shared formatter LLM speak; only the refusal branches return literal text (`chat.py`).

## Don't read
- `__pycache__/`, `tests/`.
