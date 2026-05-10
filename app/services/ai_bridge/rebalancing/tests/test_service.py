"""Cache-first rebalancing service: cache hit, cache miss, stale, blockers.

Also covers build_rebal_facts_pack and build_fallback_rebal_brief (Task 12).
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from app.services.ai_bridge.common import ensure_ai_agents_path

ensure_ai_agents_path()


# ── Task 12: build_rebal_facts_pack + build_fallback_rebal_brief ─────────────


def _build_min_response():
    """Minimal RebalancingComputeResponse reused from the conftest fixture pattern."""
    from datetime import datetime
    from decimal import Decimal

    from Rebalancing.models import (  # type: ignore[import-not-found]
        KnobSnapshot,
        RebalancingComputeResponse,
        RebalancingRunMetadata,
        RebalancingTotals,
    )
    import uuid

    return RebalancingComputeResponse(
        rows=[],
        subgroups=[],
        totals=RebalancingTotals(
            total_buy_inr=Decimal(0),
            total_sell_inr=Decimal(0),
            net_cash_flow_inr=Decimal(0),
            total_stcg_realised=Decimal(0),
            total_ltcg_realised=Decimal(0),
            total_stcg_net_off=Decimal(0),
            total_tax_estimate_inr=Decimal(0),
            total_exit_load_inr=Decimal(0),
            unrebalanced_remainder_inr=Decimal(0),
            rows_count=0,
            funds_to_buy_count=0,
            funds_to_sell_count=0,
            funds_to_exit_count=0,
            funds_held_count=0,
        ),
        metadata=RebalancingRunMetadata(
            computed_at=datetime(2026, 4, 29, 12, 0, 0),
            engine_version="test-1.0.0",
            request_corpus_inr=Decimal(0),
            knob_snapshot=KnobSnapshot(
                multi_fund_cap_pct=20.0,
                others_fund_cap_pct=10.0,
                rebalance_min_change_pct=0.10,
                exit_floor_rating=5,
                ltcg_annual_exemption_inr=Decimal("125000"),
                stcg_rate_equity_pct=20.0,
                ltcg_rate_equity_pct=12.5,
                st_threshold_months_equity=12,
                st_threshold_months_debt=24,
                multi_cap_sub_categories=[],
            ),
            request_id=uuid.uuid4(),
        ),
        trade_list=[],
    )


def _build_response_with_subgroup(holding_inr: float):
    """RebalancingComputeResponse with one SubgroupSummary carrying a non-zero holding."""
    from datetime import datetime
    from decimal import Decimal

    from Rebalancing.models import (  # type: ignore[import-not-found]
        KnobSnapshot,
        RebalancingComputeResponse,
        RebalancingRunMetadata,
        RebalancingTotals,
        SubgroupSummary,
    )
    import uuid

    sg = SubgroupSummary(
        asset_subgroup="low_beta_equities",
        goal_target_inr=Decimal(str(holding_inr)),
        current_holding_inr=Decimal(str(holding_inr)),
        suggested_final_holding_inr=Decimal(str(holding_inr)),
        rebalance_inr=Decimal(0),
        total_buy_inr=Decimal(0),
        total_sell_inr=Decimal(0),
        ranks_total=1,
        ranks_with_holding=1,
        ranks_with_action=0,
        actions=[],
    )
    return RebalancingComputeResponse(
        rows=[],
        subgroups=[sg],
        totals=RebalancingTotals(
            total_buy_inr=Decimal(0),
            total_sell_inr=Decimal(0),
            net_cash_flow_inr=Decimal(0),
            total_stcg_realised=Decimal(0),
            total_ltcg_realised=Decimal(0),
            total_stcg_net_off=Decimal(0),
            total_tax_estimate_inr=Decimal(0),
            total_exit_load_inr=Decimal(0),
            unrebalanced_remainder_inr=Decimal(0),
            rows_count=0,
            funds_to_buy_count=0,
            funds_to_sell_count=0,
            funds_to_exit_count=0,
            funds_held_count=1,
        ),
        metadata=RebalancingRunMetadata(
            computed_at=datetime(2026, 4, 29, 12, 0, 0),
            engine_version="test-1.0.0",
            request_corpus_inr=Decimal(str(holding_inr)),
            knob_snapshot=KnobSnapshot(
                multi_fund_cap_pct=20.0,
                others_fund_cap_pct=10.0,
                rebalance_min_change_pct=0.10,
                exit_floor_rating=5,
                ltcg_annual_exemption_inr=Decimal("125000"),
                stcg_rate_equity_pct=20.0,
                ltcg_rate_equity_pct=12.5,
                st_threshold_months_equity=12,
                st_threshold_months_debt=24,
                multi_cap_sub_categories=[],
            ),
            request_id=uuid.uuid4(),
        ),
        trade_list=[],
    )


def test_facts_pack_is_a_plain_dict():
    from app.services.ai_bridge.rebalancing.service import build_rebal_facts_pack

    pack = build_rebal_facts_pack(_build_min_response())
    assert isinstance(pack, dict)


def test_total_portfolio_inr_sums_current_holding():
    """total_portfolio_inr must be derived from subgroup current_holding_inr, not trade volume."""
    from app.services.ai_bridge.rebalancing.service import build_rebal_facts_pack

    pack = build_rebal_facts_pack(_build_response_with_subgroup(1_000_000))
    assert pack["total_portfolio_inr"] == 1_000_000


def test_rebal_facts_pack_zero_trades_yields_zero_trade_count():
    """Empty rows → trade_count must be exactly 0."""
    from app.services.ai_bridge.rebalancing.service import build_rebal_facts_pack

    pack = build_rebal_facts_pack(_build_min_response())
    assert pack["trade_count"] == 0


def test_facts_pack_omits_fund_and_isin():
    from app.services.ai_bridge.rebalancing.service import build_rebal_facts_pack

    pack = build_rebal_facts_pack(_build_min_response())
    blob = json.dumps(pack).lower()
    for forbidden in ("isin", "recommended_fund"):
        assert forbidden not in blob


def test_facts_pack_has_indian_siblings_for_every_inr_field():
    """Drift guard: every ``*_inr`` rupee key must have a matching ``*_indian``
    pre-formatted sibling so the chat formatter LLM never has to compute
    lakh/crore conversions (Haiku reliably gets these wrong by an order of
    magnitude).

    Walk the facts pack recursively. For each dict key ending in ``_inr``,
    assert a sibling key with the same prefix ending in ``_indian`` exists
    inside the same dict.
    """
    from app.services.ai_bridge.rebalancing.service import build_rebal_facts_pack

    pack = build_rebal_facts_pack(_build_response_with_subgroup(1_000_000))

    def walk(node, path="root"):
        if isinstance(node, dict):
            for k, v in node.items():
                if k.endswith("_inr"):
                    sibling = k[: -len("_inr")] + "_indian"
                    assert sibling in node, (
                        f"{path}: key {k!r} present but {sibling!r} sibling is missing"
                    )
                walk(v, f"{path}.{k}")
        elif isinstance(node, list):
            for i, item in enumerate(node):
                walk(item, f"{path}[{i}]")

    walk(pack)


def test_facts_pack_under_token_budget():
    from app.services.ai_bridge.rebalancing.service import build_rebal_facts_pack

    pack = build_rebal_facts_pack(_build_min_response())
    assert len(json.dumps(pack)) < 6000


def test_fallback_rebal_brief_is_non_empty():
    from app.services.ai_bridge.rebalancing.formatter import build_fallback_rebal_brief

    text = build_fallback_rebal_brief(_build_min_response(), used_cached_allocation=False)
    assert isinstance(text, str)
    assert len(text.strip()) > 0


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
    # Chart picker has been removed from the service (Plan 2 Task 8); the engine
    # response is now passed through to the brain for central chart selection.
    assert outcome.response is not None
