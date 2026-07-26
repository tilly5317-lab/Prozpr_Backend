# app/domains/cashflow/services/goal_planning_engine/ — chat bridge to the cashflow_statement engine

ORM `User` → `GoalPlanningInput` → `cashflow_statement` engine → `facts_pack` → shared answer-formatter. Reached only through the domain's `cashflow_module_service`.

## Files
- `chat.py` — `@register("goal_planning")` handler: one Haiku detector (`_detect_goal_action`) routes the turn to `narrate` / `counterfactual_explore` / `clarify`; `clarify` returns its one question without running the engine, detector failure falls back to `narrate`. Otherwise runs the engine with any `counterfactual_explore` overrides, maps refusal errors to user-facing prompts, and formats the reply (passing `action_mode` to the formatter) + chart payloads.
- `service.py` — `compute_goal_planning_snapshot`: applies any `overrides`, runs `compute_full_projection`, persists the plan run ON BASE TURNS ONLY (a counterfactual returns `plan_run_id=None`), builds `facts_pack` + deterministic `fallback_text`.
- `input_builder.py` — `build_goal_planning_input_for_user`: maps profile + properties + active goals to `GoalPlanningInput`.
- `overrides.py` — the counterfactual allow-list (`ALLOWED_OVERRIDE_KEYS`, `apply_overrides`): four keys only (`retirement_age`, `starting_monthly_investment`, `monthly_household_expense`, `one_off_outflow`); unknown key raises `ValueError`, and the return-assumption knobs are deliberately excluded. `service.py` applies it; `chat.py` only forwards the detected dict.
- `readiness.py` — `evaluate_cashflow_readiness` + `REQUIRED_CASHFLOW_FIELDS`; backs `GET /cashflow/readiness` and the builder's gate.
- `cashflow_trace.py` — structured `[AILAX_TRACE]` logging of each engine run (trigger→inputs→processing→output).
- `tests/` — pytest suite (chat, input_builder, service).

## Gotchas & invariants
- **Incomplete profiles are HARD-REFUSED, never zero-filled.** The builder evaluates readiness first and raises `missing_required_inputs:<keys>` (or `missing_date_of_birth`) so the engine only runs on real numbers — no placeholder defaults (`input_builder.py`).
- **The builder is synchronous.** It returns `(GoalPlanningInput, debug)` directly; callers must not await it — only the engine run is offloaded via `asyncio.to_thread` (`service.py`).
- **`chat.py` never templates user-visible prose for success.** It builds `facts_pack` and lets the shared formatter LLM speak; the refusal branches hand their literal message to `format_relay_or_canned` (relayed through the same formatter, verbatim only if it fails), and the `clarify` branch — returning the detector's one question — is the only reply that skips the formatter entirely (`chat.py`).
- **A counterfactual run is NEVER persisted.** `plan_run_id = None if is_counterfactual else await _persist_plan_run(...)` — every base turn writes ~30 rows and the Goal Planning screen reads the latest one, so writing a hypothetical would overwrite the customer's real plan. The gate is `bool(overrides)`; `apply_overrides` returns the SAME object when there is nothing to apply (`service.py`, `overrides.py`).

## Don't read
- `__pycache__/`, `tests/`.
