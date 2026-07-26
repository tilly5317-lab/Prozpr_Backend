"""Category resolution + status for the category-aware additional-investment chat.

Pure helpers (no DB, no LLM): canonicalise a customer's free-text category
against the ACTUAL ``sub_category`` values in the fund ranking, list the
top-ranked funds in a category, and decide where the asked category stands in
a computed deployment (spec 2026-07-04). The status vocabulary and its
precedence are contractual — the formatter prompts narrate per-status.
"""

from __future__ import annotations

from typing import Any, Optional

from app.domains.mutual_funds.services.category_resolver import (
    resolve_category,  # noqa: F401  — re-exported; public API unchanged (moved 2026-07-11)
)
from app.domains.rebalancing.services.rebal_engine.fund_rank import get_fund_ranking


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
