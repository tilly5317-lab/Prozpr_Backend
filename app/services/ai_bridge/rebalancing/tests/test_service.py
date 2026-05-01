"""Cache-first rebalancing service: cache hit, cache miss, stale, blockers."""

from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_blocks_on_missing_dob(db_session, fixture_user_no_dob):
    from app.services.ai_bridge.rebalancing.service import (
        compute_rebalancing_result,
    )

    outcome = await compute_rebalancing_result(
        user=fixture_user_no_dob,
        user_question="rebalance",
        db=db_session,
        acting_user_id=fixture_user_no_dob.id,
        chat_session_id=None,
    )
    assert outcome.blocking_message is not None
    assert (
        "date of birth" in outcome.blocking_message.lower()
        or "dob" in outcome.blocking_message.lower()
    )
    assert outcome.response is None


@pytest.mark.asyncio
async def test_blocks_on_no_holdings(db_session, fixture_user_with_dob_no_holdings):
    from app.services.ai_bridge.rebalancing.service import (
        compute_rebalancing_result,
    )

    outcome = await compute_rebalancing_result(
        user=fixture_user_with_dob_no_holdings,
        user_question="rebalance",
        db=db_session,
        acting_user_id=fixture_user_with_dob_no_holdings.id,
        chat_session_id=None,
    )
    assert outcome.blocking_message is not None
    assert "mutual fund portfolio" in outcome.blocking_message.lower()


@pytest.mark.asyncio
async def test_cache_hit_does_not_run_allocation(
    db_session,
    fixture_user_with_holdings,
    fixture_recent_allocation_row,
    fixture_seed_low_beta_navs,
    fixture_one_subgroup_ranking,
):
    """Allocation row < 90 days old → use it; do NOT call compute_allocation_result."""
    from app.services.ai_bridge.rebalancing.service import (
        compute_rebalancing_result,
    )

    user, _ = fixture_user_with_holdings
    with patch(
        "app.services.ai_bridge.rebalancing.service.compute_allocation_result",
        new=AsyncMock(),
    ) as mocked:
        outcome = await compute_rebalancing_result(
            user=user,
            user_question="rebalance",
            db=db_session,
            acting_user_id=user.id,
            chat_session_id=None,
        )
        mocked.assert_not_called()
        assert outcome.used_cached_allocation is True
        assert outcome.response is not None


@pytest.mark.asyncio
async def test_cache_miss_runs_allocation_inline(
    db_session,
    fixture_user_with_holdings,
    fixture_goal_allocation_outcome,
    fixture_seed_low_beta_navs,
    fixture_one_subgroup_ranking,
):
    """No allocation row → call compute_allocation_result, then run rebalancing."""
    from app.services.ai_bridge.rebalancing.service import (
        compute_rebalancing_result,
    )

    user, _ = fixture_user_with_holdings
    with patch(
        "app.services.ai_bridge.rebalancing.service.compute_allocation_result",
        new=AsyncMock(return_value=fixture_goal_allocation_outcome),
    ) as mocked:
        outcome = await compute_rebalancing_result(
            user=user,
            user_question="rebalance",
            db=db_session,
            acting_user_id=user.id,
            chat_session_id=None,
        )
        mocked.assert_called_once()
        assert outcome.used_cached_allocation is False
        assert outcome.response is not None


@pytest.mark.asyncio
async def test_stale_cache_re_runs_allocation(
    db_session,
    fixture_user_with_holdings,
    fixture_old_allocation_row,
    fixture_goal_allocation_outcome,
    fixture_seed_low_beta_navs,
    fixture_one_subgroup_ranking,
):
    """Allocation row > 90 days old → ignore cache, re-run."""
    from app.services.ai_bridge.rebalancing.service import (
        compute_rebalancing_result,
    )

    user, _ = fixture_user_with_holdings
    with patch(
        "app.services.ai_bridge.rebalancing.service.compute_allocation_result",
        new=AsyncMock(return_value=fixture_goal_allocation_outcome),
    ) as mocked:
        outcome = await compute_rebalancing_result(
            user=user,
            user_question="rebalance",
            db=db_session,
            acting_user_id=user.id,
            chat_session_id=None,
        )
        mocked.assert_called_once()
        assert outcome.used_cached_allocation is False


@pytest.mark.asyncio
async def test_allocation_block_propagates(
    db_session,
    fixture_user_with_holdings,
):
    """Allocation returns blocking_message → service returns the same."""
    from app.services.ai_bridge.asset_allocation.service import AllocationRunOutcome
    from app.services.ai_bridge.rebalancing.service import (
        compute_rebalancing_result,
    )

    user, _ = fixture_user_with_holdings
    blocked = AllocationRunOutcome(result=None, blocking_message="No API key.")
    with patch(
        "app.services.ai_bridge.rebalancing.service.compute_allocation_result",
        new=AsyncMock(return_value=blocked),
    ):
        outcome = await compute_rebalancing_result(
            user=user,
            user_question="rebalance",
            db=db_session,
            acting_user_id=user.id,
            chat_session_id=None,
        )
        assert outcome.blocking_message == "No API key."
        assert outcome.response is None


@pytest.mark.asyncio
async def test_persists_trades_row_on_success(
    db_session,
    fixture_user_with_holdings,
    fixture_recent_allocation_row,
    fixture_seed_low_beta_navs,
    fixture_one_subgroup_ranking,
):
    from sqlalchemy import select

    from app.models.rebalancing import (
        RebalancingRecommendation,
        RecommendationType,
    )
    from app.services.ai_bridge.rebalancing.service import (
        compute_rebalancing_result,
    )

    user, _ = fixture_user_with_holdings
    outcome = await compute_rebalancing_result(
        user=user,
        user_question="rebalance",
        db=db_session,
        acting_user_id=user.id,
        chat_session_id=None,
    )
    assert outcome.recommendation_id is not None
    rec = (await db_session.execute(
        select(RebalancingRecommendation).where(
            RebalancingRecommendation.id == outcome.recommendation_id
        )
    )).scalar_one()
    assert rec.recommendation_type == RecommendationType.REBALANCING_TRADES
    # Chart picker (default-stubbed) attaches the first candidate to the outcome.
    assert outcome.chart is not None
    assert outcome.chart.chart_type in (
        "category_gap_bar", "planned_donut", "tax_cost_bar",
    )
