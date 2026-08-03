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
- **`corpus_today` is NOT the customer's portfolio.** It merges the linked MF portfolio + direct equity + cash/debt, so it is usually LARGER than what they see on screen. The facts pack carries a `corpus_composition` split (`linked_portfolio_value`, `direct_equity_shares`, `cash_and_debt`) so the answer can distinguish "your portfolio" from "everything you hold"; quoting the merged corpus as their portfolio overstates it. The projection models only the COMBINED corpus — there is no per-component forecast, so never project one forward (`service.py`).
- **`corpus_closing` ships RAW as well as formatted; the other nine annual amounts don't.** Formatting everything is lossy for reasoning — asked "when do I cross ₹1 crore?", the model was comparing the strings `"₹98.5 lakh"` and `"₹1.02 crore"`. `corpus_closing` is the one figure compared ACROSS years, so it gets a raw value plus `corpus_closing_indian`; the rest are only ever quoted and ship `*_indian`-only (raw for all of them cost ~1,400 tokens/turn and bought nothing). General rule: format at the last moment for display, keep raw whatever the model must reason over (`service.py`).
- **The `clarify` branch has a hard-coded fallback question.** Without it an empty `clarification_question` fell through and computed a full projection for a turn the detector had said to ask about — a bug that only surfaced when `action_mode` became type-enforced (`chat.py` `_DEFAULT_CLARIFY_FALLBACK`).
- **`chat.py` never templates user-visible prose for success.** It builds `facts_pack` and lets the shared formatter LLM speak; the refusal branches hand their literal message to `format_relay_or_canned` (relayed through the same formatter, verbatim only if it fails), and the `clarify` branch — returning the detector's one question — is the only reply that skips the formatter entirely (`chat.py`).
- **A counterfactual run is NEVER persisted.** `plan_run_id = None if is_counterfactual else await _persist_plan_run(...)` — every base turn writes ~30 rows and the Goal Planning screen reads the latest one, so writing a hypothetical would overwrite the customer's real plan. The gate is `bool(overrides)`; `apply_overrides` returns the SAME object when there is nothing to apply (`service.py`, `overrides.py`).

## Don't read
- `__pycache__/`, `tests/`.
