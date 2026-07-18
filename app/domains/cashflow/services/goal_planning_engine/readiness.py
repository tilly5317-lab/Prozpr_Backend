"""Readiness check for the cashflow / goal-planning engine.

Reports which user-supplied inputs the projection needs and whether each is
present — WITHOUT substituting defaults. The engine refuses to run until every
required field is filled (see ``input_builder.build_goal_planning_input_for_user``),
so the projection never falls back to placeholder / zero-filled numbers.

This module is the single source of truth for "what does the cashflow module
need from the user". The ``GET /cashflow/readiness`` endpoint serialises it and
the frontend gate renders the unlock form straight from this list, so the
questions stay consistent between the engine and the UI.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional


@dataclass(frozen=True)
class FieldSpec:
    key: str
    label: str
    group: str
    kind: str  # "money" | "int" | "percent" | "date"
    unit: Optional[str]
    getter: Callable[[Any], Any]
    help: Optional[str] = None
    # Optional fields are surfaced in the form but do NOT block the engine.
    optional: bool = False


def _pfp(user: Any) -> Any:
    return getattr(user, "personal_finance_profile", None)


def _inv(user: Any) -> Any:
    return getattr(user, "investment_profile", None)


def _tax(user: Any) -> Any:
    return getattr(user, "tax_profile", None)


def _pfp_get(attr: str) -> Callable[[Any], Any]:
    def getter(user: Any) -> Any:
        profile = _pfp(user)
        return getattr(profile, attr, None) if profile is not None else None

    return getter


def _retirement_age(user: Any) -> Any:
    inv = _inv(user)
    return getattr(inv, "retirement_age", None) if inv is not None else None


def effective_tax_rate_value(user: Any) -> Optional[float]:
    """Marginal tax rate as a fraction (0-1). Sourced from the tax profile's
    ``income_tax_rate`` (the single source of truth, shared with /profile/complete);
    falls back to the legacy PFP ``effective_tax_rate`` for users who set it before
    the two screens were unified onto one dropdown."""
    tax = _tax(user)
    if tax is not None and getattr(tax, "income_tax_rate", None) is not None:
        return float(tax.income_tax_rate) / 100.0
    pfp = _pfp(user)
    if pfp is not None and getattr(pfp, "effective_tax_rate", None) is not None:
        return float(pfp.effective_tax_rate)
    return None


# Order here drives the order the fields appear in the unlock form.
REQUIRED_CASHFLOW_FIELDS: List[FieldSpec] = [
    FieldSpec(
        "date_of_birth",
        "Date of birth",
        "About you",
        "date",
        None,
        lambda u: getattr(u, "date_of_birth", None),
        help="Used to compute your current age and project the plan over your lifetime.",
    ),
    # Assumed lifespan is no longer asked of the user — the engine plans every
    # user to age 100 (see input_builder), so it is intentionally NOT a field here.
    FieldSpec(
        "retirement_age",
        "Planned retirement age",
        "Retirement",
        "int",
        "years",
        _retirement_age,
        help="The age you plan to stop earning a salary. Optional — a standard "
        "retirement age of 60 is assumed if left blank. The cashflow projection "
        "always runs to at least this age.",
        optional=True,
    ),
    FieldSpec(
        "annual_income",
        "Annual income",
        "Income & expenses",
        "money",
        "₹ / year",
        _pfp_get("annual_income"),
        help="Total pre-tax income across the household per year.",
    ),
    FieldSpec(
        "monthly_household_expense",
        "Monthly household expense",
        "Income & expenses",
        "money",
        "₹ / month",
        _pfp_get("monthly_household_expense"),
        help="Average monthly spend, excluding loan EMIs.",
    ),
    FieldSpec(
        "effective_tax_rate",
        "Marginal tax rate",
        "Income & expenses",
        "percent",
        "%",
        effective_tax_rate_value,
        help="Your marginal income-tax slab rate — the same figure as your profile's tax details. Optional — a standard rate is assumed if left blank.",
        optional=True,
    ),
    FieldSpec(
        "current_portfolio_corpus",
        "Current portfolio corpus",
        "Assets & liabilities",
        "money",
        "₹",
        _pfp_get("current_portfolio_corpus"),
        help="Your current mutual-fund portfolio value — prefilled from your CAMS statement / portfolio page. Added to your cash & assets as the starting corpus. When a portfolio is linked, your live portfolio value is used automatically.",
        optional=True,
    ),
    FieldSpec(
        "financial_assets",
        "Cash & debt",
        "Assets & liabilities",
        "money",
        "₹",
        _pfp_get("financial_assets"),
        help="Your cash, savings and debt holdings (FDs, bonds, debt funds), synced from your financial profile. Equities go in the field below; excludes other assets (gold, unlisted shares) and your mutual-fund portfolio corpus (above). Optional — treated as ₹0 if left blank.",
        optional=True,
    ),
    FieldSpec(
        "equity_shares",
        "Equities / shares",
        "Assets & liabilities",
        "money",
        "₹",
        _pfp_get("equity_shares"),
        help="Listed shares and equity-fund holdings you hold outside your mutual-fund portfolio (above). Optional — treated as ₹0 if left blank.",
        optional=True,
    ),
    FieldSpec(
        "financial_liabilities_excl_mortgage",
        "Liabilities (excl. mortgage)",
        "Assets & liabilities",
        "money",
        "₹",
        _pfp_get("financial_liabilities_excl_mortgage"),
        help="Outstanding debts other than your home loan. Optional — treated as ₹0 if left blank.",
        optional=True,
    ),
]


def _serialize_value(spec: FieldSpec, raw: Any) -> Optional[Any]:
    if raw is None:
        return None
    if spec.kind == "date":
        return raw.isoformat() if hasattr(raw, "isoformat") else str(raw)
    if spec.kind == "percent":
        return round(float(raw) * 100, 4)
    if spec.kind == "int":
        return int(raw)
    return float(raw)


def evaluate_cashflow_readiness(user: Any) -> Dict[str, Any]:
    """Return ``{ready, missing, fields}`` describing cashflow-input completeness."""
    fields: List[Dict[str, Any]] = []
    missing: List[str] = []
    for spec in REQUIRED_CASHFLOW_FIELDS:
        raw = spec.getter(user)
        present = raw is not None
        if not present and not spec.optional:
            missing.append(spec.key)
        fields.append(
            {
                "key": spec.key,
                "label": spec.label,
                "group": spec.group,
                "kind": spec.kind,
                "unit": spec.unit,
                "help": spec.help,
                "optional": spec.optional,
                "present": present,
                "value": _serialize_value(spec, raw),
            }
        )
    return {"ready": len(missing) == 0, "missing": missing, "fields": fields}


def missing_required_keys(user: Any) -> List[str]:
    """Required (non-optional) keys the user still has to supply."""
    return evaluate_cashflow_readiness(user)["missing"]
