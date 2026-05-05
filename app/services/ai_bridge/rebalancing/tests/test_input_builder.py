"""End-to-end input builder: holdings + allocation + CSV → RebalancingComputeRequest."""

import uuid
from decimal import Decimal

import pytest

from app.services.chat_core.turn_context import TurnContext


def _ctx_for(user, db_session) -> TurnContext:
    return TurnContext(
        user_ctx=user,
        user_question="x",
        conversation_history=[],
        client_context=None,
        session_id=uuid.uuid4(),
        db=db_session,
        effective_user_id=user.id,
        last_agent_runs={},
        active_intent="rebalancing",
        chat_overrides=None,
    )


@pytest.mark.asyncio
async def test_recommended_only_with_no_holdings(
    db_session,
    fixture_user_with_dob,
    fixture_goal_allocation_output_one_subgroup,
    fixture_seed_low_beta_navs,
    fixture_one_subgroup_ranking,
):
    """User with no MF holdings yet, allocation says ₹10L Large Cap.

    Expectation: rows are all rank ≥ 1 from CSV for that subgroup, present_* = 0,
    target_amount_pre_cap = 10L on rank 1, 0 elsewhere. total_corpus = 0.
    """
    from app.services.ai_bridge.rebalancing.input_builder import (
        build_rebalancing_input_for_user,
    )

    request, _debug = await build_rebalancing_input_for_user(
        _ctx_for(fixture_user_with_dob, db_session),
        fixture_goal_allocation_output_one_subgroup,
    )

    assert request.total_corpus == Decimal(0)
    assert all(row.is_recommended for row in request.rows)
    rank1 = next(r for r in request.rows if r.rank == 1)
    assert rank1.target_amount_pre_cap == Decimal("1000000")
    assert all(
        r.target_amount_pre_cap == Decimal(0) for r in request.rows if r.rank != 1
    )
    assert all(r.present_allocation_inr == Decimal(0) for r in request.rows)


@pytest.mark.asyncio
async def test_held_isin_in_recommended_set_enriched(
    db_session,
    fixture_user_with_holdings,
    fixture_goal_allocation_output_one_subgroup,
    fixture_seed_low_beta_navs,
    fixture_one_subgroup_ranking,
):
    """Holding maps onto a rank in the recommended set → enriched row, no BAD."""
    from app.services.ai_bridge.rebalancing.input_builder import (
        build_rebalancing_input_for_user,
    )

    user, held_isin = fixture_user_with_holdings
    request, _ = await build_rebalancing_input_for_user(
        _ctx_for(user, db_session),
        fixture_goal_allocation_output_one_subgroup,
    )

    matching = [r for r in request.rows if r.isin == held_isin]
    assert len(matching) == 1
    row = matching[0]
    assert row.is_recommended
    assert row.present_allocation_inr > Decimal(0)
    assert row.invested_cost_inr > Decimal(0)
    assert row.current_nav > Decimal(0)


@pytest.mark.asyncio
async def test_bad_fund_when_held_isin_not_recommended(
    db_session,
    fixture_user_with_bad_holding,
    fixture_goal_allocation_output_one_subgroup,
    fixture_seed_low_beta_navs,
    fixture_one_subgroup_ranking,
):
    """Holding ISIN not in fund-rank CSV → BAD row (rank=0, is_recommended=False)."""
    from app.services.ai_bridge.rebalancing.input_builder import (
        build_rebalancing_input_for_user,
    )

    request, _ = await build_rebalancing_input_for_user(
        _ctx_for(fixture_user_with_bad_holding, db_session),
        fixture_goal_allocation_output_one_subgroup,
    )

    bad_rows = [r for r in request.rows if not r.is_recommended]
    assert len(bad_rows) == 1
    bad = bad_rows[0]
    assert bad.rank == 0
    assert bad.target_amount_pre_cap == Decimal(0)
    assert bad.present_allocation_inr > Decimal(0)


@pytest.mark.asyncio
async def test_total_corpus_sums_held_market_values(
    db_session,
    fixture_user_with_two_holdings,
    fixture_goal_allocation_output_one_subgroup,
    fixture_seed_low_beta_navs,
    fixture_one_subgroup_ranking,
):
    from app.services.ai_bridge.rebalancing.input_builder import (
        build_rebalancing_input_for_user,
    )

    request, _ = await build_rebalancing_input_for_user(
        _ctx_for(fixture_user_with_two_holdings, db_session),
        fixture_goal_allocation_output_one_subgroup,
    )
    expected = (
        Decimal("10") * Decimal("60")  # holding 1: 10 units @ NAV 60 = 600
        + Decimal("5") * Decimal("80")  # holding 2: 5 units @ NAV 80 = 400
    )
    assert request.total_corpus == expected


@pytest.mark.asyncio
async def test_missing_tax_profile_uses_defaults(
    db_session,
    fixture_user_with_holdings_no_tax_profile,
    fixture_goal_allocation_output_one_subgroup,
    fixture_seed_low_beta_navs,
    fixture_one_subgroup_ranking,
):
    from app.services.ai_bridge.rebalancing.input_builder import (
        build_rebalancing_input_for_user,
    )

    request, _ = await build_rebalancing_input_for_user(
        _ctx_for(fixture_user_with_holdings_no_tax_profile, db_session),
        fixture_goal_allocation_output_one_subgroup,
    )
    assert request.tax_regime == "new"
    assert float(request.effective_tax_rate_pct) == 30.0
    assert request.carryforward_st_loss_inr == Decimal(0)
    assert request.carryforward_lt_loss_inr == Decimal(0)
    assert request.stcg_offset_budget_inr is None
    assert request.rounding_step == 100
