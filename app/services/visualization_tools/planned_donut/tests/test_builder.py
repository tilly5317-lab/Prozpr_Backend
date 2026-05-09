"""Smoke test for the planned_donut chart builder."""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

import pytest


def _make_action(present_inr: float, buy: float = 0, sell: float = 0,
                 sub_category: str = "Large Cap Fund"):
    a = MagicMock()
    a.present_allocation_inr = Decimal(str(present_inr))
    a.pass1_buy_amount = Decimal(str(buy)) if buy else None
    a.pass1_sell_amount = Decimal(str(sell)) if sell else None
    a.pass2_sell_amount = None
    a.sub_category = sub_category
    return a


def _make_response_with_two_categories():
    response = MagicMock()
    subgroup = MagicMock()
    subgroup.asset_subgroup = "low_beta_equities"
    subgroup.actions = [
        _make_action(700000, buy=100000, sub_category="Large Cap Fund"),
        _make_action(300000, sell=50000, sub_category="Mid Cap Fund"),
    ]
    response.subgroups = [subgroup]
    return response


@pytest.mark.asyncio
async def test_returns_none_when_all_zero_planned():
    from app.services.visualization_tools.planned_donut.builder import (
        build_planned_donut,
    )
    response = MagicMock()
    response.subgroups = []
    out = await build_planned_donut(response)
    assert out is None


@pytest.mark.asyncio
async def test_slices_sorted_descending():
    from app.services.visualization_tools.planned_donut.builder import (
        build_planned_donut,
    )
    out = await build_planned_donut(_make_response_with_two_categories())
    assert out is not None
    assert out.type == "planned_donut"
    assert len(out.slices) == 2
    # Large Cap = 700k - 0 + 100k = 800k; Mid Cap = 300k - 50k + 0 = 250k
    assert out.slices[0].label == "Large Cap Fund"
    assert out.slices[0].value == 800000.0
    assert out.slices[1].value == 250000.0
