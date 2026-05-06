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


def _make_fund_row(
    *,
    fund_name: str,
    sub_category: str,
    asset_subgroup: str,
    present: float,
    buy: float = 0.0,
    sell: float = 0.0,
):
    """Build a minimal FundRowAfterStep5 carrying just the fields fund_actions reads."""
    from decimal import Decimal

    from Rebalancing.models import FundRowAfterStep5  # type: ignore[import-not-found]

    return FundRowAfterStep5(
        asset_subgroup=asset_subgroup,
        sub_category=sub_category,
        recommended_fund=fund_name,
        isin=f"INF{abs(hash(fund_name)) % 10**8:08d}",
        rank=1,
        target_amount_pre_cap=Decimal(0),
        present_allocation_inr=Decimal(str(present)),
        current_nav=Decimal("100"),
        max_pct=10.0,
        target_pre_cap_pct=0.0,
        target_own_capped_pct=0.0,
        final_target_pct=0.0,
        final_target_amount=Decimal(0),
        diff=Decimal(0),
        exit_flag=False,
        worth_to_change=False,
        stcg_amount=Decimal(0),
        ltcg_amount=Decimal(0),
        exit_load_amount=Decimal(0),
        pass1_buy_amount=Decimal(str(buy)),
        pass1_underbuy_amount=Decimal(0),
        pass1_sell_amount=Decimal(str(sell)),
        pass1_undersell_amount=Decimal(0),
        pass1_sell_lt_amount=Decimal(0),
        pass1_realised_ltcg=Decimal(0),
        pass1_sell_st_amount=Decimal(0),
        pass1_realised_stcg=Decimal(0),
        stcg_budget_remaining_after_pass1=Decimal(0),
        pass1_sell_amount_no_stcg_cap=Decimal(0),
        pass1_undersell_due_to_stcg_cap=Decimal(0),
        pass1_blocked_stcg_value=Decimal(0),
        holding_after_initial_trades=Decimal(str(present + buy - sell)),
        stcg_offset_amount=Decimal(0),
        pass2_sell_amount=Decimal(0),
        pass2_undersell_amount=Decimal(0),
        final_holding_amount=Decimal(str(present + buy - sell)),
    )


def _build_response_with_funds(funds: list):
    """RebalancingComputeResponse with a single subgroup carrying multiple funds."""
    from datetime import datetime
    from decimal import Decimal
    import uuid

    from Rebalancing.models import (  # type: ignore[import-not-found]
        KnobSnapshot,
        RebalancingComputeResponse,
        RebalancingRunMetadata,
        RebalancingTotals,
        SubgroupSummary,
    )

    sg = SubgroupSummary(
        asset_subgroup="low_beta_equities",
        goal_target_inr=Decimal(0),
        current_holding_inr=Decimal(str(sum(f["present"] for f in funds))),
        suggested_final_holding_inr=Decimal(0),
        rebalance_inr=Decimal(0),
        total_buy_inr=Decimal(0),
        total_sell_inr=Decimal(0),
        ranks_total=len(funds),
        ranks_with_holding=len(funds),
        ranks_with_action=0,
        actions=[
            _make_fund_row(
                fund_name=f["name"],
                sub_category=f.get("sub_category", "Large Cap Fund"),
                asset_subgroup="low_beta_equities",
                present=f["present"],
                buy=f.get("buy", 0.0),
                sell=f.get("sell", 0.0),
            )
            for f in funds
        ],
    )
    return RebalancingComputeResponse(
        rows=list(sg.actions),
        subgroups=[sg],
        totals=RebalancingTotals(
            total_buy_inr=Decimal(0), total_sell_inr=Decimal(0),
            net_cash_flow_inr=Decimal(0), total_stcg_realised=Decimal(0),
            total_ltcg_realised=Decimal(0), total_stcg_net_off=Decimal(0),
            total_tax_estimate_inr=Decimal(0), total_exit_load_inr=Decimal(0),
            unrebalanced_remainder_inr=Decimal(0),
            rows_count=len(funds), funds_to_buy_count=0,
            funds_to_sell_count=0, funds_to_exit_count=0,
            funds_held_count=len(funds),
        ),
        metadata=RebalancingRunMetadata(
            computed_at=datetime(2026, 4, 29, 12, 0, 0),
            engine_version="test-1.0.0",
            request_corpus_inr=Decimal(0),
            knob_snapshot=KnobSnapshot(
                multi_fund_cap_pct=20.0, others_fund_cap_pct=10.0,
                rebalance_min_change_pct=0.10, exit_floor_rating=5,
                ltcg_annual_exemption_inr=Decimal("125000"),
                stcg_rate_equity_pct=20.0, ltcg_rate_equity_pct=12.5,
                st_threshold_months_equity=12, st_threshold_months_debt=24,
                multi_cap_sub_categories=[],
            ),
            request_id=uuid.uuid4(),
        ),
        trade_list=[],
    )


def test_fund_actions_includes_one_entry_per_fund():
    from app.services.ai_bridge.rebalancing.service import build_rebal_facts_pack

    response = _build_response_with_funds([
        {"name": "HDFC Top 100", "present": 500_000, "sell": 100_000},
        {"name": "ICICI Bluechip", "present": 800_000, "buy": 50_000},
    ])
    pack = build_rebal_facts_pack(response)
    actions = pack["fund_actions"]
    assert len(actions) == 2

    # Sorted biggest-first by max(current, planned_final).
    assert actions[0]["fund_name"] == "ICICI Bluechip"
    assert actions[1]["fund_name"] == "HDFC Top 100"

    icici = actions[0]
    assert icici["sub_category"] == "Large Cap Fund"
    assert icici["current_inr"] == 800_000
    assert icici["buy_inr"] == 50_000
    assert icici["sell_inr"] == 0
    assert icici["planned_final_inr"] == 850_000
    # _indian sibling present for every amount (general invariant covered by
    # test_facts_pack_has_indian_siblings_for_every_inr_field).
    assert icici["current_indian"]
    assert icici["planned_final_indian"]


def test_fund_actions_caps_at_limit_and_signals_overflow():
    from app.services.ai_bridge.rebalancing.service import (
        FUND_ACTIONS_LIMIT,
        build_rebal_facts_pack,
    )

    funds = [
        {"name": f"Fund {i:02d}", "present": 1_000_000 - i * 1_000}
        for i in range(FUND_ACTIONS_LIMIT + 5)
    ]
    pack = build_rebal_facts_pack(_build_response_with_funds(funds))
    assert len(pack["fund_actions"]) == FUND_ACTIONS_LIMIT
    assert pack["more_holdings_count"] == 5
    # Top-N selection: the largest holdings made the cut.
    assert pack["fund_actions"][0]["fund_name"] == "Fund 00"


def test_fund_actions_omits_more_holdings_count_when_under_cap():
    from app.services.ai_bridge.rebalancing.service import build_rebal_facts_pack

    pack = build_rebal_facts_pack(_build_response_with_funds([
        {"name": "Solo Fund", "present": 100_000},
    ]))
    assert "more_holdings_count" not in pack
    assert len(pack["fund_actions"]) == 1


def test_facts_pack_omits_isin():
    """ISINs must never reach the formatter LLM (customers don't read them, and
    naming an ISIN risks looking like a SEBI-flavored solicitation).

    Fund names ARE allowed via ``fund_actions`` — see fund_actions tests below.
    """
    from app.services.ai_bridge.rebalancing.service import build_rebal_facts_pack

    pack = build_rebal_facts_pack(_build_min_response())
    blob = json.dumps(pack).lower()
    assert "isin" not in blob


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


# ── goal_buckets block (goal-tied facts for the formatter) ───────────────────


def _build_alloc_output_two_buckets():
    """Minimal GoalAllocationOutput with one short-term and one long-term goal."""
    from asset_allocation_pydantic.models import (  # type: ignore[import-not-found]
        AssetClassBreakdown,
        AssetClassSplitBlock,
        BucketAllocation,
        BucketAssetClassSplit,
        ClientSummary,
        Goal,
        GoalAllocationOutput,
    )

    short_goal = Goal(
        goal_name="House down payment",
        time_to_goal_months=18,
        amount_needed=1_500_000,
        goal_priority="non_negotiable",
    )
    long_goal = Goal(
        goal_name="Retirement",
        time_to_goal_months=240,
        amount_needed=20_000_000,
        goal_priority="non_negotiable",
    )
    planned = AssetClassSplitBlock(
        per_bucket=[
            BucketAssetClassSplit(
                bucket="short_term", equity=300_000, debt=1_200_000, others=0,
                equity_pct=20.0, debt_pct=80.0, others_pct=0.0,
            ),
            BucketAssetClassSplit(
                bucket="long_term", equity=15_000_000, debt=4_000_000, others=1_000_000,
                equity_pct=75.0, debt_pct=20.0, others_pct=5.0,
            ),
        ],
        equity_total=15_300_000, debt_total=5_200_000, others_total=1_000_000,
        equity_total_pct=71.0, debt_total_pct=24.2, others_total_pct=4.7,
    )
    return GoalAllocationOutput(
        client_summary=ClientSummary(
            age=40, occupation=None, effective_risk_score=6.0,
            total_corpus=21_500_000, goals=[short_goal, long_goal],
        ),
        bucket_allocations=[
            BucketAllocation(
                bucket="short_term", goals=[short_goal],
                total_goal_amount=1_500_000, allocated_amount=1_500_000,
                subgroup_amounts={"debt_subgroup": 1_200_000, "low_beta_equities": 300_000},
            ),
            BucketAllocation(
                bucket="long_term", goals=[long_goal],
                total_goal_amount=20_000_000, allocated_amount=20_000_000,
                subgroup_amounts={
                    "low_beta_equities": 7_500_000,
                    "medium_beta_equities": 7_500_000,
                    "debt_subgroup": 4_000_000,
                    "arbitrage": 1_000_000,
                },
            ),
        ],
        aggregated_subgroups=[],
        future_investments_summary=[],
        grand_total=21_500_000,
        all_amounts_in_multiples_of_100=True,
        asset_class_breakdown=AssetClassBreakdown(
            planned=planned,
            actual=planned,
            actual_sum_matches_grand_total=True,
        ),
    )


def test_build_goal_buckets_block_shape():
    from app.services.ai_bridge.rebalancing.service import build_goal_buckets_block

    block = build_goal_buckets_block(_build_alloc_output_two_buckets())
    assert isinstance(block, list)
    assert len(block) == 2
    short = next(b for b in block if b["bucket"] == "short_term")
    long_ = next(b for b in block if b["bucket"] == "long_term")

    assert short["horizon_label"].startswith("Short-term")
    assert short["goals"][0]["name"] == "House down payment"
    assert short["goals"][0]["horizon_months"] == 18
    assert short["goals"][0]["priority"] == "non_negotiable"
    assert "amount_needed_indian" in short["goals"][0]
    assert short["planned_split_pct"] == {"equity": 20.0, "debt": 80.0, "others": 0.0}

    assert long_["horizon_label"].startswith("Long-term")
    assert long_["planned_split_pct"]["equity"] == 75.0


def test_facts_pack_includes_goal_buckets_when_provided():
    from app.services.ai_bridge.rebalancing.service import (
        build_goal_buckets_block,
        build_rebal_facts_pack,
    )

    block = build_goal_buckets_block(_build_alloc_output_two_buckets())
    pack = build_rebal_facts_pack(_build_min_response(), goal_buckets=block)
    assert pack["goal_buckets"] == block


def test_facts_pack_omits_goal_buckets_when_none():
    from app.services.ai_bridge.rebalancing.service import build_rebal_facts_pack

    pack = build_rebal_facts_pack(_build_min_response())
    assert "goal_buckets" not in pack


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
