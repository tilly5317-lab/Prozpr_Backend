"""Smoke test for the tax_cost_bar chart builder."""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

import pytest


@pytest.mark.asyncio
async def test_returns_none_when_no_taxes():
    from app.services.visualization_tools.tax_cost_bar.builder import (
        build_tax_cost_bar,
    )
    response = MagicMock()
    totals = MagicMock()
    totals.total_tax_estimate_inr = Decimal(0)
    totals.total_exit_load_inr = Decimal(0)
    response.totals = totals
    response.subgroups = []
    out = await build_tax_cost_bar(response)
    assert out is None


@pytest.mark.asyncio
async def test_includes_one_category_with_taxes():
    from app.services.visualization_tools.tax_cost_bar.builder import (
        build_tax_cost_bar,
    )
    action = MagicMock()
    action.present_allocation_inr = Decimal("500000")
    action.pass1_buy_amount = None
    action.pass1_sell_amount = Decimal("100000")
    action.pass2_sell_amount = None
    action.pass1_realised_stcg = Decimal("5000")
    action.pass1_realised_ltcg = Decimal("2000")
    action.exit_load_amount = Decimal("500")
    action.sub_category = "Mid Cap Fund"
    subgroup = MagicMock()
    subgroup.asset_subgroup = "high_beta_equities"
    subgroup.actions = [action]
    response = MagicMock()
    response.subgroups = [subgroup]
    totals = MagicMock()
    totals.total_tax_estimate_inr = Decimal("7000")
    totals.total_exit_load_inr = Decimal("500")
    response.totals = totals

    out = await build_tax_cost_bar(response)
    assert out is not None
    assert out.type == "tax_cost_bar"
    assert out.categories == ["Mid Cap Fund"]
    assert out.totals.tax_estimate_inr == 7000.0
    assert out.totals.exit_load_inr == 500.0
