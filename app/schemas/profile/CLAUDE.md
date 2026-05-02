# app/schemas/profile/

Pydantic DTOs for reading and updating a user's complete financial profile, covering
personal info, risk, tax, investment preferences, constraints, review cadence, and effective-risk results.

## Files

- `constraints.py` — `AllocationConstraintItem`, `InvestmentConstraintUpdate`, `InvestmentConstraintResponse`
- `effective_risk.py` — `EffectiveRiskAssessmentResponse`, `EffectiveRiskRecalculateResponse`
- `full_profile.py` — `FullProfileResponse` (aggregates sub-domain responses)
- `investment.py` — `InvestmentProfileUpdate`, `InvestmentProfileResponse`
- `personal.py` — `PersonalInfoUpdate`, `PersonalInfoResponse`
- `review.py` — `ReviewPreferenceUpdate`, `ReviewPreferenceResponse`
- `risk.py` — `RiskProfileUpdate`, `RiskProfileResponse`
- `tax.py` — `TaxProfileUpdate`, `TaxProfileResponse`

## Data contract

- `FullProfileResponse` — read endpoint; consumed by `app/routers/profile.py`.
- `*Update` / `*Response` pairs (personal, risk, tax, investment, constraints, review) — PATCH/GET
  sub-resource endpoints; all consumed by `app/routers/profile.py`.
- `EffectiveRiskAssessmentResponse`, `EffectiveRiskRecalculateResponse` — effective-risk read and
  recalculate endpoints; consumed by `app/routers/profile.py`.

## Depends on

- `app/models/profile/` — ORM table classes that these DTOs mirror (e.g. `risk_profile.py` exports
  `RISK_CATEGORIES` used by `risk.py`).

## Don't read

- `__pycache__/`.
