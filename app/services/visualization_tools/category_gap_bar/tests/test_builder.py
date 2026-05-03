"""Smoke test for the category_gap_bar chart builder."""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

import pytest


def _make_action(present_inr: float, buy: float = 0, sell: float = 0):
    a = MagicMock()
    a.present_allocation_inr = Decimal(str(present_inr))
    a.pass1_buy_amount = Decimal(str(buy)) if buy else None
    a.pass1_sell_amount = Decimal(str(sell)) if sell else None
    a.pass2_sell_amount = None
    a.pass1_realised_stcg = None
    a.pass1_realised_ltcg = None
    a.exit_load_amount = None
    a.sub_category = "Large Cap Fund"
    return a


def _make_response():
    """Minimal RebalancingComputeResponse-shaped MagicMock with one subgroup + one action."""
    response = MagicMock()
    subgroup = MagicMock()
    subgroup.asset_subgroup = "low_beta_equities"
    subgroup.goal_target_inr = Decimal("1100000")
    subgroup.actions = [_make_action(present_inr=1000000, buy=100000)]
    response.subgroups = [subgroup]
    return response


@pytest.mark.asyncio
async def test_returns_none_when_no_actions():
    from app.services.visualization_tools.category_gap_bar.builder import (
        build_category_gap_bar,
    )
    response = MagicMock()
    response.subgroups = []
    out = await build_category_gap_bar(response)
    assert out is None


@pytest.mark.asyncio
async def test_produces_one_category():
    from app.services.visualization_tools.category_gap_bar.builder import (
        build_category_gap_bar,
    )
    out = await build_category_gap_bar(_make_response())
    assert out is not None
    assert out.type == "category_gap_bar"
    assert out.categories == ["Large Cap Fund"]
    series_by_name = {s.name: s.values for s in out.series}
    assert "Current" in series_by_name
    assert "Target" in series_by_name
    assert "Plan" in series_by_name
    assert series_by_name["Current"][0] == 1000000.0
    assert series_by_name["Plan"][0] == 1100000.0  # current - sell + buy = 1000000 - 0 + 100000
