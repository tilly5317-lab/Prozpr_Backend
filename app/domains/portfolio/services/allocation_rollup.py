"""Single source for rolling holdings into an Equity/Debt/Others asset-class mix.

Used by the dashboard current-allocation donut (``portfolio_router``) and chat's
current-mix narration (``aa_engine``) so the two can never disagree. Blended
funds (multi-asset / hybrid) are split via the central look-through in
``scheme_classification``; everything else lands in its single canonical
asset_class. Cash / non-holding buckets are NOT handled here — callers add those
in their own vocabulary (the dashboard uses title-case ``Cash``; chat uses
lowercase ``cash``).
"""

from __future__ import annotations

from typing import Any

from app.domains.mutual_funds.services.scheme_classification import (
    add_to_asset_class_mix,
    classify_holding,
)

_DIRECT_EQUITY_ITYPES: frozenset[str] = frozenset({"equity", "stock", "share"})


def holding_single_asset_class(holding: Any) -> str:
    """Canonical single 3-bucket asset_class (``Equity`` / ``Debt`` / ``Others``)
    for one holding — the dominant class, WITHOUT the look-through split.

    Resolution order:
      1. ``classify_holding(sub_category, name)`` — SEBI sub_category lookup plus
         the ``arbitrage_plus_income`` name override.
      2. Direct-equity shortcut — non-MF holdings with ``instrument_type`` in
         {equity, stock, share} are equities by definition.
      3. Catch-all → ``Others``.
    """
    md = getattr(holding, "fund_metadata", None)
    sebi_sub = md.sub_category if md else None
    asset_class, _ = classify_holding(sebi_sub, getattr(holding, "instrument_name", None))
    if asset_class is None:
        itype = (getattr(holding, "instrument_type", None) or "").strip().lower()
        asset_class = "Equity" if itype in _DIRECT_EQUITY_ITYPES else "Others"
    return asset_class


def current_asset_class_mix(holdings: list[Any]) -> dict[str, float]:
    """Sum holdings into ``{Equity/Debt/Others: inr}``, splitting blended funds via
    the multi-asset / hybrid look-through. Total value is conserved.

    Cash / non-holding balances are not included — they have no holding to sum,
    so callers carry them forward separately.
    """
    mix: dict[str, float] = {}
    for h in holdings:
        md = getattr(h, "fund_metadata", None)
        add_to_asset_class_mix(
            mix,
            amount=float(getattr(h, "current_value", 0) or 0),
            sub_category=md.sub_category if md else None,
            fallback_asset_class=holding_single_asset_class(h),
        )
    return mix


def current_sub_category_mix(holdings: list[Any]) -> dict[str, dict[str, float]]:
    """Same rollup as ``current_asset_class_mix``, one level deeper:
    ``{Equity/Debt/Others: {sub_category: inr}}``.

    This is what the donut's slice drill-down renders. A blended fund appears
    under EVERY asset class it splits into, carrying only that class's share —
    so a Flexi Cap holding contributes 72.5% of its value to Equity's breakdown,
    17.5% to Debt's and 10% to Others'. Grouping holdings by their single
    ``asset_class`` instead would park the whole amount under the dominant class
    and leave the other buckets empty (the drill-down would then contradict the
    donut it hangs off).

    Each holding is routed through ``add_to_asset_class_mix`` — the same single
    entry point ``current_asset_class_mix`` uses — so per-class totals here sum
    back to that function's by construction, and the weights table stays the one
    source of the split rule.

    Cash / non-holding balances are not included, for the same reason as above.
    """
    mix: dict[str, dict[str, float]] = {}
    for h in holdings:
        md = getattr(h, "fund_metadata", None)
        sub_category = md.sub_category if md else None
        label = (sub_category or "").strip() or "Other"
        per_holding: dict[str, float] = {}
        add_to_asset_class_mix(
            per_holding,
            amount=float(getattr(h, "current_value", 0) or 0),
            sub_category=sub_category,
            fallback_asset_class=holding_single_asset_class(h),
        )
        for asset_class, amount in per_holding.items():
            bucket = mix.setdefault(asset_class, {})
            bucket[label] = bucket.get(label, 0.0) + amount
    return mix
