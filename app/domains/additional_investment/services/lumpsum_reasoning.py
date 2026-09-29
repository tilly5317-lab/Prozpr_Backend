"""Human-readable reasoning for a lump-sum deployment plan.

The additional-investment engine already decides WHERE fresh money goes: for a
lumpsum it runs *deficit fill* — it computes the customer's ideal portfolio for
their goals INCLUDING the new money, compares it with what they hold in each
part of the portfolio today, and directs the money into the parts furthest
below their ideal (see ``ainv_engine/service.py``). This module turns those same
facts — the per-subgroup ideal / current / gap and the plan's target horizon —
into the plain-English "why this fund" lines the Invest → Lump sum page shows.

Pure and deterministic: NO LLM, no new AI module. It only reshapes the engine's
own ``deficit_facts`` + ``target_bucket`` into customer-facing prose, so the
create path (fresh facts) and the read path (facts persisted on the run) produce
identical reasoning.
"""

from __future__ import annotations

from typing import Any, Optional

from app.domains.ai_engine.common import format_inr_indian

# Canonical asset-subgroup identifiers (scheme_classification / allocation-engine
# vocabulary) → the label the customer sees. Unknown subgroups fall back to a
# title-cased version of the raw id (``_pretty``), so a new engine subgroup never
# surfaces a raw snake_case token even before it is added here.
SUBGROUP_LABELS: dict[str, str] = {
    # Equity
    "low_beta_equities": "large-cap equity",
    "medium_beta_equities": "mid-cap & flexi-cap equity",
    "high_beta_equities": "small-cap equity",
    "value_equities": "value equity",
    "dividend_equities": "dividend-yield equity",
    "sector_equities": "sectoral & thematic equity",
    "us_equities": "US equity",
    "china_equities": "China equity",
    "multi_asset": "multi-asset funds",
    "tax_efficient_equities": "ELSS (tax-saver) equity",
    "non_mf_equities": "direct stocks",
    # Debt
    "near_debt": "liquid & overnight debt",
    "short_debt": "short-duration debt",
    "medium_debt": "medium-duration debt",
    "long_duration_debt": "long-duration debt",
    "high_risk_debt": "corporate & credit debt",
    "floating_debt": "floating-rate debt",
    "arbitrage": "arbitrage",
    "arbitrage_plus_income": "arbitrage & income",
    # Others
    "gold_commodities": "gold",
    "silver_commodities": "silver",
    "others_fofs": "other funds",
    "others": "other assets",
}

def _pretty(subgroup: str) -> str:
    """Title-case fallback label for a subgroup not in ``SUBGROUP_LABELS``."""
    return subgroup.replace("_", " ").strip() or subgroup


def subgroup_label(subgroup: str) -> str:
    """Customer-facing label for an asset subgroup."""
    return SUBGROUP_LABELS.get(subgroup, _pretty(subgroup))


def _rank_phrase(rank: int) -> str:
    """'our top-ranked' for rank 1, else 'our #N ranked'."""
    return "our top-ranked" if rank <= 1 else f"our #{rank} ranked"


def build_fund_reason(
    *,
    recommended_fund: str,
    sub_category: str,
    asset_subgroup: str,
    rank: int,
    amount_inr: float,
    deficit_row: Optional[dict[str, Any]],
) -> str:
    """One plain-English sentence on why this fund is in the plan.

    Ties the buy to (a) the customer's goal-based ideal and (b) how their current
    holdings in this part of the portfolio compare with that ideal — the exact
    logic the engine used to place the money. ``deficit_row`` is the matching
    ``deficit_facts`` entry ({ideal_inr, current_inr, gap_inr, buy_inr}); when it
    is absent (a legacy run persisted before deficit facts were stored, or a
    no-holdings plan) the reason degrades to a rank/category line without the gap.
    """
    label = subgroup_label(asset_subgroup)
    amount = format_inr_indian(amount_inr)
    rank_phrase = _rank_phrase(rank)

    if deficit_row is not None:
        ideal = float(deficit_row.get("ideal_inr", 0.0) or 0.0)
        current = float(deficit_row.get("current_inr", 0.0) or 0.0)
        gap = float(deficit_row.get("gap_inr", 0.0) or 0.0)
        if gap >= 100.0:
            return (
                f"Your {label} sits at {format_inr_indian(current)} today against a "
                f"goal-based ideal of {format_inr_indian(ideal)} — a "
                f"{format_inr_indian(gap)} shortfall. We put {amount} into "
                f"{recommended_fund}, {rank_phrase} {sub_category} pick, to help "
                "close that gap."
            )
        # At/above ideal already, but the split still routed money here (e.g. the
        # deploy pushes the post-investment ideal up): frame it as topping up.
        return (
            f"Your {label} is already close to its goal-based ideal, so {amount} "
            f"tops it up through {recommended_fund}, {rank_phrase} {sub_category} pick."
        )

    return (
        f"{amount} goes into {recommended_fund}, {rank_phrase} {sub_category} pick "
        f"for {label}, in line with your goal-based target mix."
    )


def reason_by_subgroup(
    deficit_facts: Optional[list[dict[str, Any]]],
) -> dict[str, dict[str, Any]]:
    """Index deficit facts by subgroup for per-fund reason lookup."""
    return {str(r.get("subgroup", "")): r for r in (deficit_facts or [])}


__all__ = [
    "SUBGROUP_LABELS",
    "subgroup_label",
    "build_fund_reason",
    "reason_by_subgroup",
]
