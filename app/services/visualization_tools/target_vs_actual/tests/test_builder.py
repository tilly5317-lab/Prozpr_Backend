"""Smoke test for the target_vs_actual chart builder."""
from __future__ import annotations

import uuid

import pytest

from app.models.mf.enums import PortfolioSnapshotKind
from app.models.mf.portfolio_allocation_snapshot import PortfolioAllocationSnapshot


@pytest.mark.asyncio
async def test_returns_none_when_no_target_snapshot(
    db_session, fixture_user_with_portfolio_and_allocations
):
    from app.services.visualization_tools.target_vs_actual.builder import (
        build_target_vs_actual,
    )
    out = await build_target_vs_actual(
        db_session, fixture_user_with_portfolio_and_allocations.id
    )
    assert out is None


@pytest.mark.asyncio
async def test_pairs_target_and_actual(
    db_session, fixture_user_with_portfolio_and_allocations
):
    user = fixture_user_with_portfolio_and_allocations
    snap = PortfolioAllocationSnapshot(
        id=uuid.uuid4(),
        user_id=user.id,
        snapshot_kind=PortfolioSnapshotKind.IDEAL,
        allocation={"rows": [
            {"asset_class": "Equity", "weight_pct": 60.0},
            {"asset_class": "Debt", "weight_pct": 35.0},
            {"asset_class": "Cash", "weight_pct": 5.0},
        ]},
    )
    db_session.add(snap)
    await db_session.flush()

    from app.services.visualization_tools.target_vs_actual.builder import (
        build_target_vs_actual,
    )
    out = await build_target_vs_actual(db_session, user.id)
    assert out is not None
    assert out.type == "target_vs_actual"
    by_cls = {b.asset_class: b for b in out.bars}
    assert by_cls["Equity"].target_pct == 60.0
    assert by_cls["Equity"].actual_pct == 70.0
    assert by_cls["Equity"].drift_pct == pytest.approx(10.0)
