from __future__ import annotations

from goal_based_allocation_pydantic.tables import FUND_MAPPING

# Display-name overrides for subgroups whose FUND_MAPPING.sub_category
# is ambiguous or not customer-friendly.  Everything else falls through
# to FUND_MAPPING[subgroup].sub_category automatically.
_DISPLAY_NAME_OVERRIDES: dict[str, str] = {
    "arbitrage_plus_income": "Arbitrage Income",
}


def get_display_name(subgroup: str) -> str:
    """Return the customer-facing label for *subgroup*."""
    override = _DISPLAY_NAME_OVERRIDES.get(subgroup)
    if override:
        return override
    fund = FUND_MAPPING.get(subgroup)
    if fund:
        return fund.sub_category
    return subgroup
