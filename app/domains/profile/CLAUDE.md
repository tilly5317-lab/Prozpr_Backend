# app/domains/profile/ — risk / tax / investment / constraints / personal finance / properties / review prefs / effective-risk merge

## Layers

- **models/** — per-section ORM (risk, tax, investment, investment + asset-allocation constraints, personal finance, other investments, current property, review preference, effective-risk assessment).
- **schemas/** — per-section payloads + a `FullProfileResponse` aggregator.
- **routers/** — one `/profile` router with per-section PATCH endpoints.
- **services/** — `profile_finance.py` (household-finance scalar resolver) + `personal_finance_write_service.py` (its commit-free write counterpart, so a domain owning a *derived* view of a canonical field can update it without reaching into `personal_finance_profiles`) + `_effective_risk/` (calculation, inputs, merge, service) + `preference_save_service.py` — the single investment-preference save path, serving both the screen via `preview_or_save` and chat via `resolve_one_off` / `insert_candidate` / `confirm_candidate` / `activate_candidate_for_run` (the pill-save entry, called by `rebalancing_router.save_run_as_plan`) + `preference_lexicon.py` — the word→facet table chat extraction maps `PreferenceAsk` entries through.

## Gotchas & invariants

- `profile_finance.py` is the SINGLE source of truth for household-finance scalars (income, expenses, assets/liabilities, tax rate, properties). Every engine and the IPS view reads them through its `*_pfp` helpers, sourced from `personal_finance_profiles` — never off `investment_profile` columns, which were slimmed down (`services/profile_finance.py`).
- `_effective_risk/` is the effective-risk calc, not a generic service. `merge.py` recalculates *incrementally*: a trigger (e.g. `risk_profile_update`) carries only its own input keys and everything else is carried forward from the last stored assessment — except `age`, which is ALWAYS re-derived from date of birth (`_ALWAYS_REFRESH`), so the age-only triggers (`portfolio_allocation_update`, `finvu_portfolio_sync`, `simbanks_sync`, `birthday`, `scheduled`) can shift the score with no profile section touched; `manual` or an unrecognized trigger falls back to a full refresh from DB, and calculations + output are always recomputed (`services/_effective_risk/merge.py:22-45`).
- `saved_investment_preferences` rows freeze at activation. A candidate (`activated_at` NULL) may have its target pcts / `shortfall_reason` filled after its engine run; once active, the ONLY permitted write is `is_active → false`. Both save paths deactivate the prior row and `flush()` BEFORE activating the new one — the partial unique index (`uq_saved_investment_preferences_active_user`, at most one active row per user) trips otherwise. `supersedes_id` is set once, at activation, on the new row; never write the replaced row (`services/preference_save_service.py::_persist_confirm`, `::confirm_candidate`).
- The preferences screen speaks a COMPLETE distribution (2026-09-15). Pins arrive one per settable category with blanks as explicit `0`, and `0` is a hard EXCLUSION the engine honours — while an OMITTED row still means "engine decides"; never collapse the two. `multi_asset` is validated against all three class bars at the engine's own 65/25/10 composition, not wholly as equity — the recommended sleeve is routinely ~half the portfolio, so charging it to equity alone rejected the screen's own recommendation (`services/screen_preference_service.py::_class_budget_consumed`).

## Don't read

- `__pycache__/`.
