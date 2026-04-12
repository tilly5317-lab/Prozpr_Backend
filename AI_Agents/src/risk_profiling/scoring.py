"""Deterministic risk scoring — Steps A through E from risk_profile.md."""

from typing import Any, Dict

AGE_ANCHORS = [
    (20, 10), (30, 9), (40, 8), (50, 7), (55, 6),
    (60, 5), (65, 4), (70, 3), (80, 2), (90, 1),
]

OSI_MAP = {
    "public_sector": 1.0,
    "private_sector": 0.8,
    "family_business": 0.6,
    "commission_based": 0.4,
    "freelancer_gig": 0.2,
    "retired_homemaker_student": 0.0,
}


# ── Step A: Age → Base Risk Capacity ─────────────────────────────────────────

def _age_score(age: int) -> Dict[str, Any]:
    clamped = max(20, min(90, age))

    for i in range(len(AGE_ANCHORS) - 1):
        a_low, s_low = AGE_ANCHORS[i]
        a_high, s_high = AGE_ANCHORS[i + 1]

        if clamped == a_low:
            return {
                "clamped_age": clamped,
                "age_anchor_low": f"{a_low} (score {s_low})",
                "age_anchor_high": f"{a_high} (score {s_high})",
                "age_based_score": float(s_low),
                "age_calculation": f"direct lookup at age {clamped} → {s_low}",
            }

        if a_low < clamped < a_high:
            score = round(s_low + (clamped - a_low) / (a_high - a_low) * (s_high - s_low), 4)
            calc = f"{s_low} + ({clamped}-{a_low})/({a_high}-{a_low}) * ({s_high}-{s_low}) = {score}"
            return {
                "clamped_age": clamped,
                "age_anchor_low": f"{a_low} (score {s_low})",
                "age_anchor_high": f"{a_high} (score {s_high})",
                "age_based_score": score,
                "age_calculation": calc,
            }

    # Exactly age 90
    a_low, s_low = AGE_ANCHORS[-2]
    a_high, s_high = AGE_ANCHORS[-1]
    return {
        "clamped_age": clamped,
        "age_anchor_low": f"{a_low} (score {s_low})",
        "age_anchor_high": f"{a_high} (score {s_high})",
        "age_based_score": float(s_high),
        "age_calculation": f"direct lookup at age {clamped} → {s_high}",
    }


# ── Step B: Savings Rate Adjustment ──────────────────────────────────────────

def _savings_adjustment(annual_income: float, annual_expense: float) -> Dict[str, Any]:
    if annual_income == 0:
        return {
            "savings_rate": None,
            "savings_rate_adjustment": "skipped",
            "savings_rate_boost_pct": None,
        }

    savings_rate = (annual_income - annual_expense) / annual_income

    if savings_rate < 0.01:
        return {
            "savings_rate": round(savings_rate, 4),
            "savings_rate_adjustment": "equity_reduce",
            "savings_rate_boost_pct": None,
        }

    if savings_rate > 0.20:
        boost = round(min(30.0, (savings_rate - 0.20) / (0.60 - 0.20) * 30), 2)
        return {
            "savings_rate": round(savings_rate, 4),
            "savings_rate_adjustment": "equity_boost",
            "savings_rate_boost_pct": boost,
        }

    return {
        "savings_rate": round(savings_rate, 4),
        "savings_rate_adjustment": "none",
        "savings_rate_boost_pct": None,
    }


# ── Step C: Assets & Liabilities ─────────────────────────────────────────────

def _asset_scores(
    financial_assets: float,
    liabilities_excl_mortgage: float,
    annual_expense: float,
    annual_mortgage: float,
    properties_owned: int,
) -> Dict[str, Any]:
    net_financial_assets = financial_assets - liabilities_excl_mortgage

    # Expense coverage score
    denom = annual_expense + annual_mortgage
    if financial_assets <= 0:
        expense_coverage_ratio = 0.0
        expense_coverage_score = 1.0
    elif denom <= 0:
        expense_coverage_ratio = 999.0  # effectively infinite
        expense_coverage_score = 10.0
    else:
        expense_coverage_ratio = round(financial_assets / denom, 4)
        if expense_coverage_ratio < 0.5:
            expense_coverage_score = 1.0
        elif expense_coverage_ratio > 12:
            expense_coverage_score = 10.0
        else:
            expense_coverage_score = round(1 + (expense_coverage_ratio - 0.5) / (12 - 0.5) * 9, 4)

    # Current debt score
    if financial_assets <= 0:
        current_debt_percent = 999.0
        current_debt_score = 1.0
    else:
        current_debt_percent = round(
            100 * (liabilities_excl_mortgage + annual_mortgage) / financial_assets, 4
        )
        if current_debt_percent > 100:
            current_debt_score = 1.0
        elif current_debt_percent < 6:
            current_debt_score = 10.0
        else:
            current_debt_score = round(1 + (100 - current_debt_percent) / (100 - 6) * 9, 4)

    # Property score
    own_property_score = 10.0 if properties_owned > 1 else (8.0 if properties_owned == 1 else 2.0)

    net_asset_score = round(
        0.40 * expense_coverage_score + 0.30 * current_debt_score + 0.30 * own_property_score, 4
    )

    return {
        "net_financial_assets": round(net_financial_assets, 2),
        "expense_coverage_ratio": expense_coverage_ratio,
        "expense_coverage_score": expense_coverage_score,
        "current_debt_percent": current_debt_percent,
        "current_debt_score": current_debt_score,
        "own_property_score": own_property_score,
        "net_asset_score": net_asset_score,
    }


# ── Step C cont.: Adjust Risk Capacity ───────────────────────────────────────

def _risk_capacity(age_based_score: float, net_asset_score: float) -> Dict[str, Any]:
    raw = round(age_based_score + 0.50 * (net_asset_score - 5), 4)
    clamped = round(max(1.0, min(10.0, raw)), 4)
    direction = "down" if raw > 10 else ("up" if raw < 1 else "none")
    return {
        "risk_capacity_score_raw": raw,
        "risk_capacity_score_clamped": clamped,
        "was_clamped": direction != "none",
        "clamp_direction": direction,
    }


# ── Orchestrator ──────────────────────────────────────────────────────────────

def compute_all_scores(inputs: Dict[str, Any]) -> Dict[str, Any]:
    """Run Steps A–E and return the full JSON structure (risk_summary left empty)."""
    age = inputs["age"]
    occupation_type = inputs["occupation_type"]
    annual_income = inputs["annual_income"]
    annual_expense = inputs["annual_expense"]
    financial_assets = inputs["financial_assets"]
    liabilities_excl_mortgage = inputs["liabilities_excluding_mortgage"]
    annual_mortgage = inputs["annual_mortgage_payment"]
    properties_owned = inputs["properties_owned"]
    risk_willingness = inputs["risk_willingness"]

    age_data = _age_score(age)
    savings_data = _savings_adjustment(annual_income, annual_expense)
    asset_data = _asset_scores(
        financial_assets, liabilities_excl_mortgage,
        annual_expense, annual_mortgage, properties_owned,
    )
    capacity_data = _risk_capacity(age_data["age_based_score"], asset_data["net_asset_score"])

    osi = OSI_MAP[occupation_type]
    risk_capacity_score = capacity_data["risk_capacity_score_clamped"]
    effective_risk_score = round(0.7 * risk_willingness + 0.3 * risk_capacity_score, 1)
    gap = round(abs(risk_willingness - risk_capacity_score), 3)

    calculations = {
        **age_data,
        **savings_data,
        **asset_data,
        **capacity_data,
        "osi": osi,
        "osi_category": occupation_type,
        "willingness_capacity_gap": gap,
        "gap_exceeds_3": gap > 3,
        "effective_risk_score_formula": (
            f"0.7 * {risk_willingness} + 0.3 * {risk_capacity_score} = {effective_risk_score}"
        ),
    }

    return {
        "step_name": "risk_profile",
        "inputs": inputs,
        "calculations": calculations,
        "output": {
            "effective_risk_score": effective_risk_score if effective_risk_score is not None else 7,
            "risk_summary": "",  # populated by the LLM step in chain.py
        },
    }
