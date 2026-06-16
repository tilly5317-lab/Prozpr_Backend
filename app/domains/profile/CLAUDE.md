# app/domains/profile/ — risk / tax / investment / constraints / personal finance / properties / review prefs / effective-risk merge

## Layers

- **models/** — per-section ORM (risk, tax, investment, investment + asset-allocation constraints, personal finance, other investments, current property, review preference, effective-risk assessment).
- **schemas/** — per-section payloads + a `FullProfileResponse` aggregator.
- **routers/** — one `/profile` router with per-section PATCH endpoints.
- **services/** — `profile_finance.py` (household-finance scalar resolver) + `_effective_risk/` (calculation, inputs, merge, service).

## Gotchas & invariants

- `profile_finance.py` is the SINGLE source of truth for household-finance scalars (income, expenses, assets/liabilities, tax rate, properties). Every engine and the IPS view reads them through its `*_pfp` helpers, sourced from `personal_finance_profiles` — never off `investment_profile` columns, which were slimmed down (`services/profile_finance.py`).
- `_effective_risk/` is the effective-risk calc, not a generic service. `merge.py` recalculates *incrementally*: a trigger (e.g. `risk_profile_update`) carries only its own input keys; everything else is reused from the last stored assessment, while calculations + output are always recomputed (`services/_effective_risk/merge.py`).

## Don't read

- `__pycache__/`.
