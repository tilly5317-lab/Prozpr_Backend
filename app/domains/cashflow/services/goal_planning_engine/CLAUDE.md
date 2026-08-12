# app/domains/cashflow/services/goal_planning_engine/ — chat bridge to the cashflow_statement engine

ORM `User` → `GoalPlanningInput` → `cashflow_statement` engine → `facts_pack` → shared answer-formatter. Reached only through the domain's `cashflow_module_service`.

## Files
- `chat.py` — `@register("goal_planning")` handler: a Haiku detector (`_detect_goal_action`) routes to `narrate` / `counterfactual_explore` / `clarify` (detector failure → `narrate`). `clarify` returns its one question without running the engine; the others run it (with any `counterfactual_explore` overrides), map refusals to user-facing prompts, and format the reply (passing `action_mode`) + chart payloads.
- `service.py` — `compute_goal_planning_snapshot`: applies `overrides`, runs `compute_full_projection`, persists the plan run ON BASE TURNS ONLY, builds `facts_pack` + deterministic `fallback_text`.
- `input_builder.py` — `build_goal_planning_input_for_user`: maps profile + properties + active goals to `GoalPlanningInput`.
- `overrides.py` — the counterfactual allow-list (`ALLOWED_OVERRIDE_KEYS`, `apply_overrides`): four keys only (`retirement_age`, `starting_monthly_investment`, `monthly_household_expense`, `one_off_outflow`); unknown key raises `ValueError`, return-assumption knobs deliberately excluded.
- `readiness.py` — `evaluate_cashflow_readiness` + `REQUIRED_CASHFLOW_FIELDS`; backs `GET /cashflow/readiness` and the builder's gate.
- `cashflow_trace.py` — structured `[AILAX_TRACE]` logging of each engine run.

## Gotchas & invariants
- **Incomplete profiles are HARD-REFUSED, never zero-filled.** The builder checks readiness first and raises `missing_required_inputs:<keys>` (or `missing_date_of_birth`), so the engine only runs on real numbers (`input_builder.py`).
- **The builder is synchronous** — returns `(GoalPlanningInput, debug)` directly; don't await it (only the engine run is offloaded via `asyncio.to_thread`) (`service.py`).
- **`corpus_today` is NOT the customer's portfolio.** It merges linked MF portfolio + direct equity + cash/debt, usually LARGER than the on-screen figure; the facts pack carries a `corpus_composition` split (`linked_portfolio_value`, `direct_equity_shares`, `cash_and_debt`) to separate "your portfolio" from "everything you hold". The projection models only the COMBINED corpus — never forecast a single component (`service.py`).
- **`corpus_closing` ships RAW as well as formatted; the other nine annual amounts don't.** It is the one figure compared ACROSS years (formatted strings like `"₹98.5 lakh"` vs `"₹1.02 crore"` broke "when do I cross ₹1 crore?"), so it gets a raw value plus `corpus_closing_indian`; the rest ship `*_indian`-only. Keep raw whatever the model reasons over; format at the last moment (`service.py`).
- **The `clarify` branch has a hard-coded fallback question** (`_DEFAULT_CLARIFY_FALLBACK`). Without it an empty `clarification_question` computed a full projection for a turn meant to ask a question — surfaced only when `action_mode` became type-enforced (`chat.py`).
- **`chat.py` never templates user-visible prose for success.** It builds `facts_pack` and lets the shared formatter LLM speak; refusal branches hand their literal message to `format_relay_or_canned`, and `clarify` is the only reply that skips the formatter.
- **A counterfactual run is NEVER persisted.** `plan_run_id = None if is_counterfactual else await _persist_plan_run(...)` — every base turn writes ~30 rows the Goal Planning screen reads back, so a hypothetical would overwrite the real plan. Gate: `bool(overrides)` (`service.py`, `overrides.py`).

## Don't read
- `__pycache__/`, `tests/`.
