# app/domains/profile/ — risk / tax / investment / constraints / personal finance / properties / other assets / review prefs / effective-risk merge

Risk / tax / investment / constraints / personal finance / properties / other assets / review prefs / effective-risk merge.

## Layers

- **models/** — per-section ORM (risk_profile, tax_profile, investment_profile, investment_constraint, asset_allocation_constraint, personal_finance_profile, other_investment, user_current_property, review_preference, effective_risk_assessment)
- **schemas/** — per-section payloads + a `FullProfileResponse` aggregator
- **routers/** — single `/profile` router with per-section PATCH endpoints
- **services/** — profile_service.read_full / update_* + _effective_risk/ helpers (calculation, inputs, merge, service)

## Don't read

- `__pycache__/`.
