# app/models/profile/

User profile tables covering risk tolerance, tax situation, investment constraints,
personal finance figures, other assets held, currently owned properties with
mortgage state, and review cadence preferences. Column-level detail:
`README_DATABASE_SCHEMA.md`.

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
- `user_current_property.py` — `UserCurrentProperty`

## Tables

- `investment_constraints` — `InvestmentConstraint`; stores per-user investment constraint settings. Relationships: belongs to User; has many AssetAllocationConstraints.
- `asset_allocation_constraints` — `AssetAllocationConstraint`; per-asset-class allocation bounds for a constraint record. Relationships: belongs to InvestmentConstraint.
- `effective_risk_assessments` — `EffectiveRiskAssessment`; computed or advisor-set effective risk score for a user. Relationships: belongs to User.
- `investment_profiles` — `InvestmentProfile`; user's investment goals, horizon, and preferences. Relationships: belongs to User.
- `other_investments` — `OtherInvestment`; non-platform assets (gold, FDs, etc.) declared by the user. Real-estate inputs for the cashflow engine live in `user_current_properties` instead. Relationships: belongs to User.
- `personal_finance_profiles` — `PersonalFinanceProfile`; merged onboarding + cashflow `ClientProfile` row (income / expense ranges, wealth sources, plus `annual_income`, `effective_tax_rate`, `financial_assets`, `financial_liabilities_excl_mortgage`, `monthly_household_expense`, `starting_monthly_investment`). New cashflow fields are nullable until populated. Relationships: belongs to User.
- `review_preferences` — `ReviewPreference`; user's preferred portfolio review frequency and channels. Relationships: belongs to User.
- `risk_profiles` — `RiskProfile`; questionnaire-derived risk score and category. Relationships: belongs to User.
- `tax_profiles` — `TaxProfile`; tax bracket, regime, and exemption details. Relationships: belongs to User.
- `user_current_properties` — `UserCurrentProperty`; user-owned existing properties with optional mortgage state (EMI + end date). Specialised cashflow-engine input; conceptually an extension of `OtherInvestment` but kept as its own table for the property-specific columns. Relationships: belongs to User.

## Depends on

- `app/models/user.py` — User hub; all tables here carry a `users.id` foreign key.

## Don't read

- `__pycache__/`.
