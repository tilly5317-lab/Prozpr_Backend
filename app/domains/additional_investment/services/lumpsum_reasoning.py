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

# Asset class per subgroup — used only for the alignment-row grouping on the
# page. Kept small and local (mirrors asset_allocation_pydantic.tables), since
# importing that AI-Agents table here would drag sys.path wiring into a pure
# reshaping helper.
_SUBGROUP_ASSET_CLASS: dict[str, str] = {
    "low_beta_equities": "Equity",
    "medium_beta_equities": "Equity",
    "high_beta_equities": "Equity",
    "value_equities": "Equity",
    "dividend_equities": "Equity",
    "sector_equities": "Equity",
    "us_equities": "Equity",
    "china_equities": "Equity",
    "multi_asset": "Equity",
    "tax_efficient_equities": "Equity",
    "non_mf_equities": "Equity",
    "near_debt": "Debt",
    "short_debt": "Debt",
    "medium_debt": "Debt",
    "long_duration_debt": "Debt",
    "high_risk_debt": "Debt",
    "floating_debt": "Debt",
    "arbitrage": "Debt",
    "arbitrage_plus_income": "Debt",
    "gold_commodities": "Others",
    "silver_commodities": "Others",
    "others_fofs": "Others",
    "others": "Others",
}

# Plain-English framing of the horizon the deploy leaned toward. The engine's
# ``target_bucket`` is the customer's NEAREST UNFUNDED goal (short → medium →
# long); we never surface the raw label.
_BUCKET_GOAL_PHRASE: dict[str, str] = {
    "short_term": "your nearer-term goals (under ~3 years)",
    "medium_term": "your medium-term goals (about 3–6 years away)",
    "long_term": "your long-term wealth goals",
}


def _pretty(subgroup: str) -> str:
    """Title-case fallback label for a subgroup not in ``SUBGROUP_LABELS``."""
    return subgroup.replace("_", " ").strip() or subgroup


def subgroup_label(subgroup: str) -> str:
    """Customer-facing label for an asset subgroup."""
    return SUBGROUP_LABELS.get(subgroup, _pretty(subgroup))


def asset_class_for(subgroup: str) -> str:
    """Asset class (Equity / Debt / Others) for a subgroup; 'Others' if unknown."""
    return _SUBGROUP_ASSET_CLASS.get(subgroup, "Others")


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


def headline_reason(
    target_bucket: Optional[str], deployed_inr: float, undeployed_inr: float
) -> str:
    """One-line summary of the whole deployment, in goal terms.

    Explains the gap-fill spirit and which goal horizon the money leaned toward,
    plus a note when a material amount could not be placed.
    """
    goal_phrase = _BUCKET_GOAL_PHRASE.get(target_bucket or "", "your goals")
    base = (
        f"We compared where your portfolio is against where it should be for your "
        f"goals and steered this {format_inr_indian(deployed_inr)} into the biggest "
        f"gaps — weighted toward {goal_phrase}."
    )
    if undeployed_inr >= 100.0:
        base += (
            f" {format_inr_indian(undeployed_inr)} couldn't be placed (per-fund caps "
            "or a shortage of eligible funds); your emergency reserve is always kept "
            "out of fresh deployment."
        )
    return base


def build_alignment_rows(
    deficit_facts: Optional[list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Reshape ``deficit_facts`` into the page's alignment rows.

    One row per subgroup the money went into, biggest deploy first, carrying the
    friendly label, asset class, and the ideal / current / gap / deploy figures
    the "why these funds" section renders. Empty when there are no deficit facts
    (legacy or no-holdings run) — the page then just omits the alignment section.
    """
    rows: list[dict[str, Any]] = []
    for row in deficit_facts or []:
        subgroup = str(row.get("subgroup", ""))
        rows.append(
            {
                "subgroup": subgroup,
                "label": subgroup_label(subgroup),
                "asset_class": asset_class_for(subgroup),
                "ideal_inr": float(row.get("ideal_inr", 0.0) or 0.0),
                "current_inr": float(row.get("current_inr", 0.0) or 0.0),
                "gap_inr": float(row.get("gap_inr", 0.0) or 0.0),
                "deploy_inr": float(row.get("buy_inr", 0.0) or 0.0),
            }
        )
    # Only rows that actually received money are interesting to the customer.
    rows = [r for r in rows if r["deploy_inr"] > 0]
    rows.sort(key=lambda r: r["deploy_inr"], reverse=True)
    return rows


def reason_by_subgroup(
    deficit_facts: Optional[list[dict[str, Any]]],
) -> dict[str, dict[str, Any]]:
    """Index deficit facts by subgroup for per-fund reason lookup."""
    return {str(r.get("subgroup", "")): r for r in (deficit_facts or [])}


__all__ = [
    "SUBGROUP_LABELS",
    "subgroup_label",
    "asset_class_for",
    "build_fund_reason",
    "headline_reason",
    "build_alignment_rows",
    "reason_by_subgroup",
]
