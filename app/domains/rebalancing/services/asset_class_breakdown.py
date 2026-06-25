"""Invest-page asset-class breakdown (current vs target) with multi-asset look-through.

The Invest "Current vs Target" bars need an Equity/Debt/Others split that treats
blended multi-asset / hybrid funds correctly (not 100% Equity):

- CURRENT is computed by the caller from the portfolio holdings via the shared
  ``current_asset_class_mix`` (the same rollup the dashboard + chat use), so the
  three surfaces agree.
- TARGET is built here from the rebalancing plan's per-subgroup ``suggested_final``
  totals. The engine's ``multi_asset`` sleeve is a GENERIC multi-asset allocation
  sized at 65/25/10 — so it is split by that composition (matching the engine
  ideal that chat shows), regardless of which specific funds the rebalancer picks
  to fill it (those can be hybrids, dynamic-allocation, or even plain equity funds).
  Splitting by the recommended funds' own categories would drop the sleeve's
  Others/Debt slices whenever an equity-heavy fund fills it.
"""

from __future__ import annotations

from typing import Any, Iterable

from app.domains.mutual_funds.services.scheme_classification import (
    ASSET_CLASS_EQUITY,
    add_to_asset_class_mix,
    asset_class_for_subgroup,
)

MULTI_ASSET_SUBGROUP = "multi_asset"

# The engine sizes its multi_asset sleeve as a generic Multi-Asset Allocation —
# its canonical sub_category — so we split the sleeve by that band (65/25/10),
# which is exactly what the asset-allocation engine (and chat) report.
_MULTI_ASSET_SLEEVE_SUBCATEGORY = "Multi-Asset Allocation Fund"


def target_asset_class_mix(subgroup_summaries: Iterable[Any]) -> dict[str, float]:
    """Roll the rebalancing TARGET (``suggested_final_holding_inr``) up to
    Equity/Debt/Others.

    Every subgroup maps via ``asset_class_for_subgroup`` EXCEPT ``multi_asset``,
    which is the engine's generic multi-asset sleeve and is split by the canonical
    Multi-Asset Allocation composition (65/25/10) so the Invest target aligns with
    the engine ideal shown in chat.
    """
    mix: dict[str, float] = {}
    for summary in subgroup_summaries:
        subgroup = getattr(summary, "asset_subgroup", None)
        amount = float(getattr(summary, "suggested_final_holding_inr", 0.0) or 0.0)
        if subgroup == MULTI_ASSET_SUBGROUP:
            add_to_asset_class_mix(
                mix,
                amount=amount,
                sub_category=_MULTI_ASSET_SLEEVE_SUBCATEGORY,
                fallback_asset_class=ASSET_CLASS_EQUITY,
            )
        else:
            asset_class = asset_class_for_subgroup(subgroup)
            mix[asset_class] = mix.get(asset_class, 0.0) + amount
    return mix
