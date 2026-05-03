"""Smoke test for the buy_sell_ledger chart builder."""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

import pytest


def _make_action(name: str, sub_cat: str, buy: float = 0, sell: float = 0):
    a = MagicMock()
    # Set the fund-identity attr to whatever the actual model uses.
    # Set ALL plausible names so the builder's getattr chain finds one of them.
    a.recommended_fund = name
    a.fund_name = name
    a.name = name
    a.instrument_name = name
    a.scheme_name = name
    a.sub_category = sub_cat
    a.pass1_buy_amount = Decimal(str(buy)) if buy else None
    a.pass1_sell_amount = Decimal(str(sell)) if sell else None
    a.pass2_sell_amount = None
    a.present_allocation_inr = Decimal("100000")
    return a


@pytest.mark.asyncio
async def test_returns_none_when_no_trades():
    from app.services.visualization_tools.buy_sell_ledger.builder import (
        build_buy_sell_ledger,
    )
    response = MagicMock()
    response.subgroups = []
    out = await build_buy_sell_ledger(response)
    assert out is None


@pytest.mark.asyncio
async def test_returns_rows_sorted_by_absolute_trade():
    from app.services.visualization_tools.buy_sell_ledger.builder import (
        build_buy_sell_ledger,
    )
    subgroup = MagicMock()
    subgroup.actions = [
        _make_action("Fund A", "Large Cap Fund", buy=200000),
        _make_action("Fund B", "Mid Cap Fund", sell=80000),
        _make_action("Fund C", "Large Cap Fund", buy=10000),
    ]
    response = MagicMock()
    response.subgroups = [subgroup]
    out = await build_buy_sell_ledger(response)
    assert out is not None
    assert out.type == "buy_sell_ledger"
    assert len(out.rows) == 3
    # Sorted by abs(buy + sell): Fund A 200k, Fund B 80k, Fund C 10k
    assert out.rows[0].name == "Fund A"
    assert out.rows[0].buy_inr == 200000.0
    assert out.rows[1].name == "Fund B"
    assert out.rows[1].sell_inr == 80000.0
