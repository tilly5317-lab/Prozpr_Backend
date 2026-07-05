"""Category resolution + status for the category-aware additional-investment chat.

Pure helpers (no DB, no LLM): canonicalise a customer's free-text category
against the ACTUAL ``sub_category`` values in the fund ranking, list the
top-ranked funds in a category, and decide where the asked category stands in
a computed deployment (spec 2026-07-04). The status vocabulary and its
precedence are contractual — the formatter prompts narrate per-status.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from app.domains.rebalancing.services.rebal_engine.fund_rank import get_fund_ranking

# Free-text synonyms → canonical ranking sub_category. Keys are matched as
# whole words/phrases (word-boundary regex) of the customer's category text (longest key first, so
# "small cap" wins over "cap"). Extend as categories join the ranking.
_CATEGORY_SYNONYMS: dict[str, str] = {
    "small cap": "Small Cap Fund",
    "smallcap": "Small Cap Fund",
    "mid cap": "Mid Cap Fund",
    "midcap": "Mid Cap Fund",
    "large cap": "Large Cap Fund",
    "largecap": "Large Cap Fund",
    "bluechip": "Large Cap Fund",
    "blue chip": "Large Cap Fund",
    "flexi cap": "Flexi Cap Fund",
    "flexicap": "Flexi Cap Fund",
    "multi cap": "Flexi Cap Fund",
    "multicap": "Flexi Cap Fund",
    "elss": "ELSS",
    "tax saving": "ELSS",
    "tax saver": "ELSS",
    "80c": "ELSS",
    "gold": "Gold ETF",
    "arbitrage": "Arbitrage Fund",
    "value fund": "Value Fund",
    "contra": "Contra Fund",
    "multi asset": "Multi Asset Allocation",
    "balanced advantage": "Dynamic Asset Allocation or Balanced Advantage",
    "dynamic asset": "Dynamic Asset Allocation or Balanced Advantage",
    "aggressive hybrid": "Aggressive Hybrid Fund",
    "hybrid": "Aggressive Hybrid Fund",
    "short duration": "Short Duration Fund",
    "debt fund": "Short Duration Fund",
    "international": "FoF Overseas",
    "overseas": "FoF Overseas",
    "us fund": "FoF Overseas",
    "nasdaq": "FoF Overseas",
    "fof": "FoF Domestic",
}


def _ranking_categories() -> set[str]:
    """The sub_category values actually present in the ranking (live, not
    hardcoded — a synonym target absent from the ranking resolves to None)."""
    return {
        row.sub_category
        for rows in get_fund_ranking().values()
        for row in rows
        if row.sub_category
    }


def resolve_category(text: Optional[str]) -> Optional[str]:
    """Canonicalise free text to a ranking sub_category, or None.

    Direct case-insensitive match first, then longest-synonym substring match.
    Only categories that EXIST in the ranking are returned — unknown asks stay
    None so the reply can honestly say "we don't rank funds there".
    """
    if not text or not text.strip():
        return None
    needle = text.strip().lower()
    present = _ranking_categories()
    for canonical in present:
        if canonical.lower() == needle:
            return canonical
    for key in sorted(_CATEGORY_SYNONYMS, key=len, reverse=True):
        if re.search(rf"\b{re.escape(key)}\b", needle):
            target = _CATEGORY_SYNONYMS[key]
            return target if target in present else None
    return None


def _category_rows(category: str) -> list[Any]:
    rows = [
        row
        for subgroup_rows in get_fund_ranking().values()
        for row in subgroup_rows
        if row.sub_category == category
    ]
    return sorted(rows, key=lambda r: r.rank)


def top_funds_for_category(category: str, n: int = 3) -> list[Any]:
    """Top-N ranking rows in the category, rank-ascending."""
    return _category_rows(category)[:n]


def category_subgroup(category: str) -> Optional[str]:
    """The asset_subgroup the category deploys through — the top-ranked fund's
    subgroup (contractual tie-break for categories spanning subgroups)."""
    rows = _category_rows(category)
    return rows[0].asset_subgroup if rows else None


def category_status(
    category: Optional[str],
    *,
    deficit_facts: Optional[list[dict[str, Any]]],
    buys: list[Any],
    exclude_subgroups: set[str],
) -> str:
    """Where the asked category stands in the computed plan.

    Precedence (first match wins — spec 2026-07-04):
      not_ranked → excluded_by_policy → in_plan → (gap-gated) at_or_above_ideal
      / subgroup_funded_other_funds; gap>0 with no buy, or deficit_facts None
      → plan_by_goals.
    """
    if category is None:
        return "not_ranked"
    subgroup = category_subgroup(category)
    if subgroup in exclude_subgroups:
        return "excluded_by_policy"
    if any(getattr(b, "sub_category", None) == category for b in buys):
        return "in_plan"
    if deficit_facts is None:
        return "plan_by_goals"
    row = next((r for r in deficit_facts if r.get("subgroup") == subgroup), None)
    if row is None or float(row.get("gap_inr", 0.0)) <= 0:
        return "at_or_above_ideal"
    if float(row.get("buy_inr", 0.0)) > 0:
        return "subgroup_funded_other_funds"
    # gap > 0 but nothing placed there (sub-₹100 rounding, caps, or fund
    # scarcity): neither "funded" nor "at/above ideal" is true — degrade to the
    # generic goals narration, which is never false (final review 2026-07-04).
    return "plan_by_goals"
