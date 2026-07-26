# app/domains/profile/ — risk / tax / investment / constraints / personal finance / properties / review prefs / effective-risk merge

## Layers

- **models/** — per-section ORM (risk, tax, investment, investment + asset-allocation constraints, personal finance, other investments, current property, review preference, effective-risk assessment).
- **schemas/** — per-section payloads + a `FullProfileResponse` aggregator.
- **routers/** — one `/profile` router with per-section PATCH endpoints.
- **services/** — `profile_finance.py` (household-finance scalar resolver) + `_effective_risk/` (calculation, inputs, merge, service).

## Gotchas & invariants

- `profile_finance.py` is the SINGLE source of truth for household-finance scalars (income, expenses, assets/liabilities, tax rate, properties). Every engine and the IPS view reads them through its `*_pfp` helpers, sourced from `personal_finance_profiles` — never off `investment_profile` columns, which were slimmed down (`services/profile_finance.py`).
- `_effective_risk/` is the effective-risk calc, not a generic service. `_effective_risk/` is the effective-risk calc, not a generic service. `merge.py` recalculates *incrementally*: a trigger (e.g. `risk_profile_update`) carries only its own input keys and everything else is carried forward from the last stored assessment — except `age`, which is ALWAYS re-derived from date of birth (`_ALWAYS_REFRESH`), so the age-only triggers (`portfolio_allocation_update`, `finvu_portfolio_sync`, `simbanks_sync`, `birthday`, `scheduled`) can shift the score with no profile section touched; `manual` or an unrecognized trigger falls back to a full refresh from DB, and calculations + output are always recomputed (`services/_effective_risk/merge.py:22-45`).

## Don't read

- `__pycache__/`.
