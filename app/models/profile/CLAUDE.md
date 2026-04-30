# app/models/profile/

User profile tables covering risk tolerance, tax situation, investment constraints,
personal finance figures, other assets held, and review cadence preferences.
Column-level detail: `README_DATABASE_SCHEMA.md`.

## Files

- `asset_allocation_constraint.py` — `AssetAllocationConstraint`
- `effective_risk_assessment.py` — `EffectiveRiskAssessment`
- `investment_constraint.py` — `InvestmentConstraint`
- `investment_profile.py` — `InvestmentProfile`
- `other_investment.py` — `OtherInvestment`
- `personal_finance_profile.py` — `PersonalFinanceProfile`
- `review_preference.py` — `ReviewPreference`
- `risk_profile.py` — `RiskProfile`
- `tax_profile.py` — `TaxProfile`

## Tables

- `investment_constraints` — `InvestmentConstraint`; stores per-user investment constraint settings. Relationships: belongs to User; has many AssetAllocationConstraints.
- `asset_allocation_constraints` — `AssetAllocationConstraint`; per-asset-class allocation bounds for a constraint record. Relationships: belongs to InvestmentConstraint.
- `effective_risk_assessments` — `EffectiveRiskAssessment`; computed or advisor-set effective risk score for a user. Relationships: belongs to User.
- `investment_profiles` — `InvestmentProfile`; user's investment goals, horizon, and preferences. Relationships: belongs to User.
- `other_investments` — `OtherInvestment`; non-platform assets (property, gold, FDs, etc.) declared by the user. Relationships: belongs to User.
- `personal_finance_profiles` — `PersonalFinanceProfile`; income, expenses, and savings-rate data. Relationships: belongs to User.
- `review_preferences` — `ReviewPreference`; user's preferred portfolio review frequency and channels. Relationships: belongs to User.
- `risk_profiles` — `RiskProfile`; questionnaire-derived risk score and category. Relationships: belongs to User.
- `tax_profiles` — `TaxProfile`; tax bracket, regime, and exemption details. Relationships: belongs to User.

## Depends on

- `app/models/user.py` — User hub; all tables here carry a `users.id` foreign key.

## Don't read

- `__pycache__/`.

## Refresh

If stale, run `/refresh-context` from this folder.
