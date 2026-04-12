"""Effective risk profile — `calculation.py`.

App-layer persistence and calculation helpers for the user’s effective risk assessment (distinct from the deterministic ``risk_profiling.scoring`` used when building ``AllocationInput`` for ideal allocation).
"""


from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Optional

OccupationType = Literal[
    "public_sector",
    "private_sector",
    "family_business",
    "commission_based",
    "freelancer_gig",
    "retired_homemaker_student",
]

OSI_BY_OCCUPATION: dict[str, float] = {
    "public_sector": 1.0,
    "private_sector": 0.8,
    "family_business": 0.6,
    "commission_based": 0.4,
    "freelancer_gig": 0.2,
    "retired_homemaker_student": 0.0,
}

OSI_CATEGORY_LABEL: dict[str, str] = {
    "public_sector": "Public sector",
    "private_sector": "Private sector job",
    "family_business": "Family business",
    "commission_based": "Commission-based job",
    "freelancer_gig": "Freelancer / Gig worker",
    "retired_homemaker_student": "Retired / Homemaker / Student",
}

# (age, risk_capacity_score)
_AGE_ANCHORS: list[tuple[float, float]] = [
    (20, 10),
    (30, 9),
    (40, 8),
    (50, 7),
    (55, 6),
    (60, 5),
    (65, 4),
    (70, 3),
    (80, 2),
    (90, 1),
]


@dataclass(frozen=True)
class EffectiveRiskComputationInput:
    age: float
    occupation_type: str
    annual_income: float
    annual_expense: float
    financial_assets: float
    liabilities_excluding_mortgage: float
    annual_mortgage_payment: float
    properties_owned: int
    risk_willingness: float


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _interpolate_age_score(age: float) -> tuple[float, float, str, str, str]:
    """Return age_based_score, clamped_age, anchor_low, anchor_high, age_calculation."""
    clamped = _clamp(age, 20.0, 90.0)
    # Exact anchor hit
    for a, s in _AGE_ANCHORS:
        if abs(clamped - a) < 1e-9:
            s_low = f"{int(a)} (score {s})"
            s_high = s_low
            calc = f"Anchor age {int(a)} → score {s}"
            return float(s), float(clamped), s_low, s_high, calc

    for i in range(len(_AGE_ANCHORS) - 1):
        a_lo, s_lo = _AGE_ANCHORS[i]
        a_hi, s_hi = _AGE_ANCHORS[i + 1]
        if a_lo <= clamped <= a_hi:
            if a_hi == a_lo:
                score = float(s_lo)
            else:
                score = s_lo + (clamped - a_lo) / (a_hi - a_lo) * (s_hi - s_lo)
            anchor_low = f"{int(a_lo)} (score {s_lo})"
            anchor_high = f"{int(a_hi)} (score {s_hi})"
            calc = (
                f"{s_lo} + ({clamped:.4g}-{a_lo})/({a_hi}-{a_lo}) * ({s_hi}-{s_lo}) = {score:.6g}"
            )
            return score, float(clamped), anchor_low, anchor_high, calc

    # Fallback (should not happen for clamped 20–90)
    return 5.0, float(clamped), "?", "?", "fallback"


def _savings_rate_adjustment(
    savings_rate: Optional[float],
    skipped: bool,
) -> tuple[str, Optional[float], str]:
    """Returns (adjustment_kind, boost_pct_or_none, description)."""
    if skipped or savings_rate is None:
        return "skipped", None, "No regular income / savings rate skipped"
    sr = savings_rate
    if sr < 0.01:
        return "equity_reduce", None, "savings_rate < 1% → reduce equity 10–20% from midpoint"
    if sr <= 0.20:
        return "none", None, "savings_rate between 1% and 20%"
    # sr > 20%: proportional boost 10–30%, max at 60%+
    if sr <= 0.60:
        boost = 10.0 + (sr - 0.20) / (0.60 - 0.20) * (30.0 - 10.0)
    else:
        boost = 30.0
    return "equity_boost", round(boost, 4), f"equity_boost_pct≈{boost:.2f}% from midpoint"


def _expense_coverage(financial_assets: float, annual_expense: float, annual_mortgage_payment: float) -> tuple[float, float]:
    denom = annual_expense + annual_mortgage_payment
    if denom <= 0:
        return 0.0, 10.0
    ratio = financial_assets / denom
    if ratio < 0.5:
        return ratio, 1.0
    if ratio > 12:
        return ratio, 10.0
    score = 1.0 + (ratio - 0.5) / (12.0 - 0.5) * 9.0
    return ratio, score


def _current_debt_score(
    financial_assets: float,
    liabilities_excluding_mortgage: float,
    annual_mortgage_payment: float,
) -> tuple[float, float]:
    if financial_assets <= 0:
        return 200.0, 1.0
    num = liabilities_excluding_mortgage + annual_mortgage_payment
    pct = 100.0 * num / financial_assets
    if pct > 100:
        return pct, 1.0
    if pct < 6:
        return pct, 10.0
    score = 1.0 + (100.0 - pct) / (100.0 - 6.0) * 9.0
    return pct, score


def _own_property_score(properties_owned: int) -> float:
    if properties_owned > 1:
        return 10.0
    if properties_owned == 1:
        return 8.0
    return 2.0


def _risk_label(effective: float) -> str:
    if effective >= 8:
        return "Aggressive"
    if effective >= 6.5:
        return "Moderately aggressive"
    if effective >= 5:
        return "Moderate"
    if effective >= 3.5:
        return "Moderately conservative"
    return "Conservative"


def _build_risk_summary(
    effective: float,
    capacity_clamped: float,
    willingness: float,
    gap_exceeds: bool,
) -> str:
    label = _risk_label(effective)
    line1 = (
        f"{label} investor (effective score {effective:.2f}). "
        f"Risk willingness {willingness:.2f} and computed capacity {capacity_clamped:.2f}."
    )
    if gap_exceeds:
        line2 = (
            "Willingness and capacity differ by more than 3 points — keep equity and high-beta "
            "exposure below the midpoint of their ranges."
        )
    else:
        line2 = "Willingness and capacity are reasonably aligned for allocation within the model ranges."
    return f"{line1} {line2}"


def compute_effective_risk_document(inp: EffectiveRiskComputationInput) -> dict[str, Any]:
    """
    Build the full persisted JSON: step_name, inputs, calculations, output.

    ``risk_willingness`` is the user-declared value on a 1–10 scale (not the derived capacity).
    """
    occ = inp.occupation_type if inp.occupation_type in OSI_BY_OCCUPATION else "private_sector"
    osi = OSI_BY_OCCUPATION[occ]
    osi_label = OSI_CATEGORY_LABEL[occ]

    age_based, clamped_age_val, anchor_low, anchor_high, age_calc = _interpolate_age_score(inp.age)

    # Savings rate
    skip_savings = inp.occupation_type == "retired_homemaker_student" or inp.annual_income <= 0
    savings_rate: Optional[float]
    if skip_savings:
        savings_rate = None
    else:
        savings_rate = (inp.annual_income - inp.annual_expense) / inp.annual_income

    sav_adj, sav_boost_pct, _sav_desc = _savings_rate_adjustment(savings_rate, skip_savings)

    net_financial_assets = inp.financial_assets - inp.liabilities_excluding_mortgage

    exp_ratio, exp_score = _expense_coverage(
        inp.financial_assets, inp.annual_expense, inp.annual_mortgage_payment
    )
    debt_pct, debt_score = _current_debt_score(
        inp.financial_assets,
        inp.liabilities_excluding_mortgage,
        inp.annual_mortgage_payment,
    )
    own_score = _own_property_score(inp.properties_owned)

    net_asset_score = 0.40 * exp_score + 0.30 * debt_score + 0.30 * own_score

    risk_capacity_raw = age_based + 0.50 * (net_asset_score - 5.0)
    capacity_clamped = _clamp(risk_capacity_raw, 1.0, 10.0)
    was_clamped = abs(risk_capacity_raw - capacity_clamped) > 1e-9
    if risk_capacity_raw < 1.0:
        clamp_dir = "up"
    elif risk_capacity_raw > 10.0:
        clamp_dir = "down"
    else:
        clamp_dir = "none"

    willingness = _clamp(inp.risk_willingness, 1.0, 10.0)
    eff = (willingness + capacity_clamped) / 2.0
    gap = abs(willingness - capacity_clamped)
    gap_exceeds = gap > 3.0

    formula_str = (
        f"(risk_willingness + risk_capacity_score) / 2 = ({willingness:.6g} + {capacity_clamped:.6g}) / 2 = {eff:.6g}"
    )

    doc: dict[str, Any] = {
        "step_name": "risk_profile",
        "inputs": {
            "age": inp.age,
            "occupation_type": occ,
            "annual_income": inp.annual_income,
            "annual_expense": inp.annual_expense,
            "financial_assets": inp.financial_assets,
            "liabilities_excluding_mortgage": inp.liabilities_excluding_mortgage,
            "annual_mortgage_payment": inp.annual_mortgage_payment,
            "properties_owned": inp.properties_owned,
            "risk_willingness": willingness,
        },
        "calculations": {
            "clamped_age": clamped_age_val,
            "age_anchor_low": anchor_low,
            "age_anchor_high": anchor_high,
            "age_based_score": age_based,
            "age_calculation": age_calc,
            "savings_rate": savings_rate,
            "savings_rate_adjustment": sav_adj,
            "savings_rate_boost_pct": sav_boost_pct,
            "net_financial_assets": net_financial_assets,
            "expense_coverage_ratio": exp_ratio,
            "expense_coverage_score": exp_score,
            "current_debt_percent": debt_pct,
            "current_debt_score": debt_score,
            "own_property_score": own_score,
            "net_asset_score": net_asset_score,
            "risk_capacity_score_raw": risk_capacity_raw,
            "risk_capacity_score_clamped": capacity_clamped,
            "was_clamped": was_clamped,
            "clamp_direction": clamp_dir,
            "osi": osi,
            "osi_category": osi_label,
            "willingness_capacity_gap": gap,
            "gap_exceeds_3": gap_exceeds,
            "effective_risk_score_formula": formula_str,
        },
        "output": {
            "effective_risk_score": eff,
            "risk_summary": _build_risk_summary(eff, capacity_clamped, willingness, gap_exceeds),
        },
    }
    return doc


def risk_willingness_from_risk_level(risk_level: Optional[int]) -> Optional[float]:
    """Map legacy 0–4 risk_level to 1–10 willingness when explicit willingness is not stored."""
    if risk_level is None:
        return None
    if not (0 <= risk_level <= 4):
        return None
    return 1.0 + risk_level * (9.0 / 4.0)
