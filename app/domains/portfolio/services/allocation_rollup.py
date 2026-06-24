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
