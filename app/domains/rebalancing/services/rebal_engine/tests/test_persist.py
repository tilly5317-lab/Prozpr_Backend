"""Persist rebalancing engine output as a normalized ``RebalancingRun`` row.

Rewritten 2026-07-06: the original asserted the long-gone
``RebalancingRecommendation`` JSON-blob model; the write surface is now
``persist_rebalancing_recommendation`` → ``rebalancing_runs`` + children.
"""

import uuid

import pytest


@pytest.mark.asyncio
async def test_persist_writes_run_row_with_source_fk(
    db_session,
    fixture_user_with_dob,
    fixture_rebalancing_response,
    fixture_allocation_row,
):
    from app.domains.rebalancing.services.rebalancing_persist_service import (
        persist_rebalancing_recommendation,
    )

    run_id = await persist_rebalancing_recommendation(
        db_session,
        fixture_user_with_dob.id,
        fixture_rebalancing_response,
        source_allocation_run_id=fixture_allocation_row.id,
        chat_session_id=None,
        used_cached_allocation=True,
        user_question="rebalance my portfolio",
    )
    assert isinstance(run_id, uuid.UUID)

    from sqlalchemy import select

    from app.domains.rebalancing.models.rebalancing_run import (
        RebalancingRun,
        RebalancingRunStatus,
    )

    run = (
        await db_session.execute(
            select(RebalancingRun).where(RebalancingRun.id == run_id)
        )
    ).scalar_one()
    assert run.user_id == fixture_user_with_dob.id
    assert run.source_allocation_run_id == fixture_allocation_row.id
    assert run.status == RebalancingRunStatus.pending
    assert run.engine_version == fixture_rebalancing_response.metadata.engine_version
    assert run.used_cached_allocation is True
    assert run.user_question == "rebalance my portfolio"
    # KnobSnapshot (incl. the 2026-07-06 fund_cap_floor_inr) survives the JSONB dump.
    assert "fund_cap_floor_inr" in run.knob_snapshot


@pytest.mark.asyncio
async def test_persist_writes_totals_child_row(
    db_session,
    fixture_user_with_dob,
    fixture_rebalancing_response,
    fixture_allocation_row,
):
    from sqlalchemy import select

    from app.domains.rebalancing.models.rebalancing_run import RebalancingTotals
    from app.domains.rebalancing.services.rebalancing_persist_service import (
        persist_rebalancing_recommendation,
    )

    run_id = await persist_rebalancing_recommendation(
        db_session,
        fixture_user_with_dob.id,
        fixture_rebalancing_response,
        source_allocation_run_id=fixture_allocation_row.id,
    )
    totals = (
        await db_session.execute(
            select(RebalancingTotals).where(RebalancingTotals.run_id == run_id)
        )
    ).scalar_one()
    assert totals.rows_count == 0
    assert totals.total_buy_inr == 0
