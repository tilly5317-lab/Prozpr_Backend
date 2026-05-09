"""Smoke test for the top_bottom_funds chart builder."""
from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from app.models.portfolio import Portfolio, PortfolioHolding


@pytest.mark.asyncio
async def test_returns_none_when_no_holdings(
    db_session, fixture_user_with_dob,
):
    from app.services.visualization_tools.top_bottom_funds.builder import (
        build_top_bottom_funds,
    )
    out = await build_top_bottom_funds(db_session, fixture_user_with_dob.id)
    assert out is None


@pytest.mark.asyncio
async def test_returns_top_3_and_bottom_3_by_return_1y(
    db_session, fixture_user_with_portfolio_and_allocations,
):
    from sqlalchemy import select
    from app.services.visualization_tools.top_bottom_funds.builder import (
        build_top_bottom_funds,
    )
    user = fixture_user_with_portfolio_and_allocations
    portfolio = (await db_session.execute(
        select(Portfolio).where(Portfolio.user_id == user.id)
    )).scalar_one()

    # 8 holdings with returns from -10% to 25%
    returns = [25.0, 18.0, 14.0, 10.0, 5.0, 0.0, -3.0, -10.0]
    for i, r in enumerate(returns):
        db_session.add(PortfolioHolding(
            id=uuid.uuid4(),
            portfolio_id=portfolio.id,
            instrument_name=f"Fund {i+1}",
            instrument_type="mutual_fund",
            current_value=Decimal("100000"),
            return_1y=Decimal(str(r)),
        ))
    await db_session.flush()

    out = await build_top_bottom_funds(db_session, user.id)
    assert out is not None
    assert out.type == "top_bottom_funds"
    assert len(out.top) == 3
    assert len(out.bottom) == 3
    assert out.top[0].name == "Fund 1"  # 25%
    assert out.top[0].return_pct == 25.0
    assert out.bottom[-1].name == "Fund 8"  # -10%
    # average is over all funds with return_1y set
    assert out.portfolio_average_pct == pytest.approx(sum(returns) / 8, abs=0.5)
