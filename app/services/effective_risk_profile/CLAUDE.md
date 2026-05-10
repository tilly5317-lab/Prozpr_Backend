# app/services/effective_risk_profile/ — Effective risk profile persistence + calculation

Persistence and calculation helpers for the user's effective risk assessment
(effective score, savings-rate adjustment, over-saving index). Distinct from
`AI_Agents/src/risk_profiling/` which is the deterministic scoring pipeline.

## Files

- `calculation.py` — `EffectiveRiskComputationInput`, `compute_effective_risk_document`,
  `risk_willingness_from_risk_level`; thin shim over `AI_Agents` risk profiling chain.
- `inputs.py` — `build_computation_input`; derives computation inputs from ORM profile rows.
- `merge.py` — `merge_computation_inputs`; merges prior inputs with updated user overrides.
- `service.py` — `upsert_effective_risk_assessment`, `maybe_recalculate_effective_risk`;
  async persistence and conditional recalculation triggered by profile changes.
- `__init__.py` — re-exports the public surface consumed by routers.

## Entry point

- `upsert_effective_risk_assessment`, `maybe_recalculate_effective_risk` (from `service.py`)
  — imported by `app/routers/profile.py`, `app/routers/portfolio.py`,
  `app/routers/onboarding.py`, and `app/routers/simbanks.py`.
- `EffectiveRiskComputationInput`, `compute_effective_risk_document`,
  `risk_willingness_from_risk_level` — also re-exported via `__init__.py`.

## Depends on

- `AI_Agents/src/risk_profiling/` — `risk_profiling_chain.invoke` and `OSI_MAP` (injected
  via `sys.path`).
- `app/models/` — `EffectiveRiskAssessment`, `RiskProfile`, `InvestmentProfile`,
  `PersonalFinanceProfile`.

## Don't read

- `__pycache__/`.
