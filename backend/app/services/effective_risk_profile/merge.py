"""
Incremental input merge for effective risk recalculation.

When a trigger fires, we **replace only the input variables that belong to that trigger**
(and always refresh **age** from date of birth). All other input fields are **carried forward**
from the last stored assessment so values stay stable unless that part of the profile changed.

The **calculations** and **output** blocks are always recomputed from the merged inputs (full
deterministic pass) so scores stay internally consistent.
"""

from __future__ import annotations

from typing import Any, FrozenSet

from app.services.effective_risk_profile.calculation import EffectiveRiskComputationInput

# Trigger names must match ``trigger_reason`` passed from routers (prefix before truncation).
# Empty set = only ``age`` is refreshed from DB; all other inputs come from the previous snapshot.
_MERGE_KEYS_BY_TRIGGER: dict[str, FrozenSet[str]] = {
    "investment_profile_update": frozenset(
        {
            "annual_income",
            "annual_expense",
            "financial_assets",
            "liabilities_excluding_mortgage",
            "annual_mortgage_payment",
            "properties_owned",
        }
    ),
    "risk_profile_update": frozenset({"occupation_type", "risk_willingness"}),
    "onboarding_profile_update": frozenset({"age", "annual_income", "annual_expense"}),
    "portfolio_allocation_update": frozenset(),
    "finvu_portfolio_sync": frozenset(),
    "simbanks_sync": frozenset(),
    # Age-only refresh (same as empty set); explicit for cron / birthday jobs
    "birthday": frozenset(),
    "scheduled": frozenset(),
}

# Always take current age from DOB for every recalculation (time passes; birthdays).
_ALWAYS_REFRESH: FrozenSet[str] = frozenset({"age"})


def computation_input_to_inputs_dict(inp: EffectiveRiskComputationInput) -> dict[str, Any]:
    return {
        "age": inp.age,
        "occupation_type": inp.occupation_type,
        "annual_income": inp.annual_income,
        "annual_expense": inp.annual_expense,
        "financial_assets": inp.financial_assets,
        "liabilities_excluding_mortgage": inp.liabilities_excluding_mortgage,
        "annual_mortgage_payment": inp.annual_mortgage_payment,
        "properties_owned": inp.properties_owned,
        "risk_willingness": inp.risk_willingness,
    }


def inputs_dict_to_computation_input(d: dict[str, Any]) -> EffectiveRiskComputationInput:
    return EffectiveRiskComputationInput(
        age=float(d["age"]),
        occupation_type=str(d["occupation_type"]),
        annual_income=float(d["annual_income"]),
        annual_expense=float(d["annual_expense"]),
        financial_assets=float(d["financial_assets"]),
        liabilities_excluding_mortgage=float(d["liabilities_excluding_mortgage"]),
        annual_mortgage_payment=float(d["annual_mortgage_payment"]),
        properties_owned=int(d["properties_owned"]),
        risk_willingness=float(d["risk_willingness"]),
    )


def merge_computation_inputs(
    previous_inputs: dict[str, Any] | None,
    fresh_from_db: EffectiveRiskComputationInput,
    trigger_reason: str,
) -> EffectiveRiskComputationInput:
    """
    Merge previous persisted ``inputs`` with values freshly derived from the DB.

    - No previous snapshot, ``manual`` recalculation, or unknown trigger → use **fresh_from_db** only.
    - Otherwise → start from ``previous_inputs``, overwrite keys for this trigger + ``age``.
    """
    tr = (trigger_reason or "").strip()
    if not previous_inputs:
        return fresh_from_db

    # Full refresh: explicit manual run or anything we do not treat as incremental.
    if tr == "manual" or tr not in _MERGE_KEYS_BY_TRIGGER:
        return fresh_from_db

    fresh_d = computation_input_to_inputs_dict(fresh_from_db)
    merged: dict[str, Any] = dict(previous_inputs)

    for k in _ALWAYS_REFRESH:
        merged[k] = fresh_d[k]

    for k in _MERGE_KEYS_BY_TRIGGER[tr]:
        merged[k] = fresh_d[k]

    return inputs_dict_to_computation_input(merged)
