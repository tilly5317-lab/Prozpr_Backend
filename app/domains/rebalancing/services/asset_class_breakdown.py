"""Invest-page asset-class breakdown (current vs target) with multi-asset look-through.

The Invest "Current vs Target" bars need an Equity/Debt/Others split that treats
blended multi-asset / hybrid funds correctly (not 100% Equity):

- CURRENT is computed by the caller from the portfolio holdings via the shared
  ``current_asset_class_mix`` (the same rollup the dashboard + chat use), so the
  three surfaces agree.
- TARGET is built here from the rebalancing plan's per-subgroup ``suggested_final``
  totals. The engine's ``multi_asset`` sleeve is a GENERIC multi-asset allocation
  sized by the canonical ``DEFAULT_MULTI_ASSET_COMPOSITION_PCTS`` (65/25/10,
  equity/debt/others) — so it is split by that composition (matching the engine
  ideal that chat shows), regardless of which specific funds the rebalancer picks
  to fill it (those can be hybrids, dynamic-allocation, or even plain equity funds).
  Splitting by the recommended funds' own categories would drop the sleeve's
  Others/Debt slices whenever an equity-heavy fund fills it.
"""

from __future__ import annotations

from typing import Any, Iterable

from app.domains.ai_engine.common import ensure_ai_agents_path
from app.domains.mutual_funds.services.scheme_classification import (
    ASSET_CLASS_DEBT,
    ASSET_CLASS_EQUITY,
    ASSET_CLASS_OTHERS,
    asset_class_for_subgroup,
)

ensure_ai_agents_path()
from asset_allocation_pydantic.tables import (  # type: ignore[import-not-found]  # noqa: E402
    DEFAULT_MULTI_ASSET_COMPOSITION_PCTS,
)

MULTI_ASSET_SUBGROUP = "multi_asset"

# The engine sizes its multi_asset sleeve by DEFAULT_MULTI_ASSET_COMPOSITION_PCTS
# (equity, debt, others) — the same 65/25/10 band chat reports — so we split the
# sleeve by that composition, sourced from the engine constant so it can't drift.
_SLEEVE_EQUITY_PCT, _SLEEVE_DEBT_PCT, _SLEEVE_OTHERS_PCT = (
    DEFAULT_MULTI_ASSET_COMPOSITION_PCTS
)


def _subgroup_mix(subgroup_summaries: Iterable[Any], attr: str) -> dict[str, float]:
    """Roll one ₹ column of the subgroup summaries up to Equity/Debt/Others.

    Every subgroup maps via ``asset_class_for_subgroup`` EXCEPT ``multi_asset``,
    which is the engine's generic multi-asset sleeve and is split by the canonical
    engine composition (65/25/10) so the Invest bars align with the engine ideal
    shown in chat.
    """
    mix: dict[str, float] = {}
    for summary in subgroup_summaries:
        subgroup = getattr(summary, "asset_subgroup", None)
        amount = float(getattr(summary, attr, 0.0) or 0.0)
        if subgroup == MULTI_ASSET_SUBGROUP:
            mix[ASSET_CLASS_EQUITY] = (
                mix.get(ASSET_CLASS_EQUITY, 0.0)
                + amount * _SLEEVE_EQUITY_PCT / 100.0
            )
            mix[ASSET_CLASS_DEBT] = (
                mix.get(ASSET_CLASS_DEBT, 0.0) + amount * _SLEEVE_DEBT_PCT / 100.0
            )
            mix[ASSET_CLASS_OTHERS] = (
                mix.get(ASSET_CLASS_OTHERS, 0.0)
                + amount * _SLEEVE_OTHERS_PCT / 100.0
            )
        else:
            asset_class = asset_class_for_subgroup(subgroup)
            mix[asset_class] = mix.get(asset_class, 0.0) + amount
    return mix


def target_asset_class_mix(subgroup_summaries: Iterable[Any]) -> dict[str, float]:
    """Rebalancing TARGET (``suggested_final_holding_inr``) as Equity/Debt/Others."""
    return _subgroup_mix(subgroup_summaries, "suggested_final_holding_inr")


def run_current_asset_class_mix(subgroup_summaries: Iterable[Any]) -> dict[str, float]:
    """The run's own CURRENT (``current_holding_inr``) as Equity/Debt/Others.

    Uses the same valuation basis the engine sized the plan on, so the
    current-vs-target bars are directly comparable: their totals differ only by
    the plan's net cash flow (≈0), never by a statement-NAV vs today's-NAV gap.
    The ``portfolio_holdings`` rollup (statement NAVs) can sit several percent
    away from the engine's transaction-derived, today's-NAV totals, which made
    the Current bar render visibly shorter than the Target bar.
    """
    return _subgroup_mix(subgroup_summaries, "current_holding_inr")
