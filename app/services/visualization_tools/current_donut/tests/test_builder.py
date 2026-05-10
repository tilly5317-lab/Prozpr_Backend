"""Smoke test for the current_donut chart builder."""
from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_returns_none_when_no_portfolio(db_session, fixture_user_with_dob):
    from app.services.visualization_tools.current_donut.builder import (
        build_current_donut,
    )

    out = await build_current_donut(db_session, fixture_user_with_dob.id)
    assert out is None


@pytest.mark.asyncio
async def test_donut_slices_match_allocations(
    db_session, fixture_user_with_portfolio_and_allocations
):
    from app.services.visualization_tools.current_donut.builder import (
        build_current_donut,
    )

    user = fixture_user_with_portfolio_and_allocations
    out = await build_current_donut(db_session, user.id)
    assert out is not None
    assert out.type == "current_donut"
    labels = {s.label for s in out.slices}
    assert labels == {"Equity", "Debt", "Cash"}
    pcts = {s.label: s.percentage for s in out.slices}
    assert pcts["Equity"] + pcts["Debt"] + pcts["Cash"] == pytest.approx(100.0, abs=0.5)
    assert out.total_value > 0
