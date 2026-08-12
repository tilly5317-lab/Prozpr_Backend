"""Comply-and-caution deviation numbers for constraint-aware consolidation.

Deterministic. Two lenses (logic audit 2026-07-11):
  * asset-class mix (target vs unconstrained vs constrained) — the right lens
    for big swings ("only gold"), but can read flat for intra-equity asks;
  * buy_mix_by_category — % of the buy budget per sub_category, which always
    moves when the customer's category/count constraint bites. The formatter
    picks whichever lens actually moved.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any


def _planned_mix_pct(response) -> dict[str, float]:
    """Asset-class mix of the PLANNED final holdings, reusing the same rollup
    build_rebal_facts_pack produces (target_asset_class_mix_pct).

    Reads the TARGET block. It used to read ``asset_class_mix_pct``, which was
    the current-holdings mix despite this function's name — so the constrained
    vs unconstrained comparison was computed on holdings that neither variant
    would end up with, and both lenses moved only as far as the sells did.
    """
    from app.domains.rebalancing.services.rebal_engine.service import (
        build_rebal_facts_pack,
    )

    return build_rebal_facts_pack(response).get("target_asset_class_mix_pct", {})


def _target_mix_pct(response) -> dict[str, float]:
    """Ideal asset-class mix from the practical allocation's planned breakdown.

    Same source the facts pack ships as ``ideal_asset_class_mix_pct``; kept at one
    decimal here because the deviation deltas below are computed from it.
    """
    from app.domains.rebalancing.services.rebal_engine.service import (
        ideal_asset_class_mix_pct,
    )

    mix = ideal_asset_class_mix_pct(response)
    if mix is None:
        return {}
    return {cls: round(value, 1) for cls, value in mix.items()}


def _buy_mix_by_category(response) -> dict[str, float]:
    """% of the BUY budget per sub_category — the lens the constraints act on."""
    by_cat: dict[str, Decimal] = {}
    total = Decimal(0)
    for r in response.rows:
        buy = Decimal(getattr(r, "pass1_buy_amount", 0) or 0)
        if buy > 0:
            cat = r.sub_category or r.asset_subgroup
            by_cat[cat] = by_cat.get(cat, Decimal(0)) + buy
            total += buy
    if total <= 0:
        return {}
    return {k: round(float(v / total * 100), 1) for k, v in by_cat.items()}


def build_constraint_impact(original, reshaped, *, risk_profile: str | None) -> dict[str, Any]:
    target = _target_mix_pct(original)
    unconstrained = _planned_mix_pct(original)
    constrained = _planned_mix_pct(reshaped)
    keys = set(target) | set(unconstrained) | set(constrained)
    deviations = sorted(
        ([k, round(float(constrained.get(k, 0.0)) - float(target.get(k, 0.0)), 1)]
         for k in keys),
        key=lambda kv: abs(kv[1]), reverse=True,
    )[:5]
    return {
        "target_mix_pct": target,
        "unconstrained_mix_pct": unconstrained,
        "constrained_mix_pct": constrained,
        "largest_deviations": deviations,
        "buy_mix_by_category": {
            "unconstrained": _buy_mix_by_category(original),
            "constrained": _buy_mix_by_category(reshaped),
        },
        "risk_profile": risk_profile,
    }
