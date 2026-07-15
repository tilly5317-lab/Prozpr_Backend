"""Shared free-text → canonical fund-category resolver.

The ONE mapping for every chat module (rebalancing consolidation,
additional-investment focus-category, and future surfaces) — taxonomy lives in
`mutual_funds`, same pattern as `scheme_classification.py`. Moved here from
`additional_investment/services/ainv_engine/category.py` (2026-07-11) so the
rebalancing consolidation flow can canonicalise a customer's category words
without duplicating the synonym table. `additional_investment.category`
re-exports `resolve_category`, so its public API is unchanged.

Pure: no DB, no LLM. Canonical targets are the ACTUAL `sub_category` values in
the fund ranking (a synonym target absent from the ranking resolves to None).
"""

from __future__ import annotations

import re
from typing import Optional

from app.domains.rebalancing.services.rebal_engine.fund_rank import get_fund_ranking

# Free-text synonyms → canonical ranking sub_category. Keys are matched as
# whole words/phrases (word-boundary regex) of the customer's category text
# (longest key first, so "small cap" wins over "cap"). Extend as categories
# join the ranking.
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


def resolve_categories(texts: list[str]) -> tuple[list[str], list[str]]:
    """Resolve many free-text categories at once.

    Returns (resolved canonical sub_categories, unresolved input words). The
    resolved list is de-duplicated and order-preserving; unresolved collects
    every input that mapped to None so the caller can clarify honestly.
    """
    resolved: list[str] = []
    unresolved: list[str] = []
    for t in texts:
        hit = resolve_category(t)
        if hit is None:
            unresolved.append(t)
        elif hit not in resolved:
            resolved.append(hit)
    return resolved, unresolved
