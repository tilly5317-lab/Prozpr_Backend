"""Smoke test for the concentration_risk chart builder."""
from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from app.models.portfolio import PortfolioHolding


@pytest.mark.asyncio
async def test_returns_none_when_no_holdings(
    db_session, fixture_user_with_portfolio_and_allocations
):
    from app.services.visualization_tools.concentration_risk.builder import (
        build_concentration_risk,
    )
    user = fixture_user_with_portfolio_and_allocations
    out = await build_concentration_risk(db_session, user.id)
    assert out is None  # the fixture has allocations but no holdings


@pytest.mark.asyncio
async def test_top_n_severity(db_session, fixture_user_with_portfolio_and_allocations):
    from app.services.visualization_tools.concentration_risk.builder import (
        build_concentration_risk,
    )
    user = fixture_user_with_portfolio_and_allocations
    # Look up the portfolio created by the fixture
    from sqlalchemy import select
    from app.models.portfolio import Portfolio
    portfolio = (await db_session.execute(
        select(Portfolio).where(Portfolio.user_id == user.id)
    )).scalar_one()

    # 6 holdings: top-1 = 60%, others ~8%, so severity should be "watch" or "act"
    values = [Decimal(s) for s in ("600000", "80000", "80000", "80000", "80000", "80000")]
    for i, v in enumerate(values):
        db_session.add(PortfolioHolding(
            id=uuid.uuid4(),
            portfolio_id=portfolio.id,
            instrument_name=f"Fund {i+1}",
            instrument_type="mutual_fund",
            current_value=v,
        ))
    await db_session.flush()

    out = await build_concentration_risk(db_session, user.id)
    assert out is not None
    assert out.type == "concentration_risk"
    assert out.top_n == 5
    assert out.severity in {"watch", "act"}
    assert out.top_holdings[0].label == "Fund 1"
    assert out.rest_count == 1
