"""Persist rebalancing engine output as a REBALANCING_TRADES row."""

import uuid

import pytest

from app.models.rebalancing import RebalancingStatus, RecommendationType


@pytest.mark.asyncio
async def test_persist_writes_trades_row_with_source_fk(
    db_session,
    fixture_user_with_dob,
    fixture_rebalancing_response,
    fixture_allocation_row,
):
    from app.services.rebalancing_recommendation_persist import (
        persist_rebalancing_recommendation,
    )

    rec_id = await persist_rebalancing_recommendation(
        db_session,
        fixture_user_with_dob.id,
        fixture_rebalancing_response,
        chat_session_id=None,
        source_allocation_id=fixture_allocation_row.id,
        used_cached_allocation=True,
    )
    assert isinstance(rec_id, uuid.UUID)

    from sqlalchemy import select
    from app.models.rebalancing import RebalancingRecommendation

    rec = (await db_session.execute(
        select(RebalancingRecommendation).where(RebalancingRecommendation.id == rec_id)
    )).scalar_one()
    assert rec.recommendation_type == RecommendationType.REBALANCING_TRADES
    assert rec.source_allocation_id == fixture_allocation_row.id
    assert rec.status == RebalancingStatus.pending
    assert "rebalancing_response" in rec.recommendation_data
    assert rec.recommendation_data["used_cached_allocation"] is True
