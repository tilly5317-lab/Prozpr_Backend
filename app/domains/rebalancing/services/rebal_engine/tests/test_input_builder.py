"""End-to-end input builder: holdings + allocation + CSV → RebalancingComputeRequest."""

import uuid
from decimal import Decimal

import pytest

from app.domains.ai_engine.turn_context import TurnContext


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
    from app.domains.rebalancing.services.rebal_engine.input_builder import (
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
    from app.domains.rebalancing.services.rebal_engine.input_builder import (
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
    from app.domains.rebalancing.services.rebal_engine.input_builder import (
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
    from app.domains.rebalancing.services.rebal_engine.input_builder import (
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
    from app.domains.rebalancing.services.rebal_engine.input_builder import (
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


def test_short_term_holdings_from_rows_count_held_debt_only():
    from types import SimpleNamespace

    from app.domains.rebalancing.services.rebal_engine.input_builder import (
        _short_term_holdings_from_rows,
    )

    rows = [
        SimpleNamespace(asset_subgroup="near_debt", present_allocation_inr=Decimal("300000")),
        SimpleNamespace(asset_subgroup="near_debt", present_allocation_inr=Decimal("100000")),
        SimpleNamespace(asset_subgroup="arbitrage", present_allocation_inr=Decimal("200000")),
        SimpleNamespace(asset_subgroup="arbitrage_plus_income", present_allocation_inr=Decimal("400000")),
        SimpleNamespace(asset_subgroup="low_beta_equities", present_allocation_inr=Decimal("900000")),
        SimpleNamespace(asset_subgroup="short_debt", present_allocation_inr=Decimal("0")),
    ]
    assert _short_term_holdings_from_rows(rows) == 600_000.0


@pytest.mark.asyncio
async def test_practical_input_takes_short_term_holdings_from_rows(
    monkeypatch,
    db_session,
    fixture_user_with_two_holdings,
    fixture_goal_allocation_output_one_subgroup,
    fixture_seed_low_beta_navs,
    fixture_one_subgroup_ranking,
):
    from app.domains.practical_asset_allocation.services.paa_engine import (
        input_builder as paa_ib,
    )
    from app.domains.rebalancing.services.rebal_engine.input_builder import (
        build_rebalancing_input_for_user,
    )

    def _boom(user):
        raise AssertionError("rebalancing must value holdings from its ledger rows")

    monkeypatch.setattr(paa_ib, "short_term_holdings_for_user", _boom)
    request, _ = await build_rebalancing_input_for_user(
        _ctx_for(fixture_user_with_two_holdings, db_session),
        fixture_goal_allocation_output_one_subgroup,
    )
    assert request.practical_allocation_input.short_term_holdings == 0.0


@pytest.mark.asyncio
async def test_held_elss_is_frozen_and_never_traded(
    db_session,
    fixture_user_with_dob,
    fixture_goal_allocation_output_one_subgroup,
    fixture_seed_low_beta_navs,
    fixture_one_subgroup_ranking,
):
    """ELSS sits under a 3-year lock-in: it reaches the engine only as the practical
    input's frozen elss_corpus, never as a tradeable row, and stays in the total."""
    from datetime import date

    from app.domains.ai_engine.common import ensure_ai_agents_path
    from app.domains.rebalancing.services.rebal_engine.input_builder import (
        build_rebalancing_input_for_user,
    )

    from .conftest import _RANK1_ISIN, _add_holding

    ensure_ai_agents_path()
    from Rebalancing.pipeline import run_rebalancing  # type: ignore[import-not-found]

    user = fixture_user_with_dob
    await _add_holding(
        db_session, user=user, scheme_code=f"SCH_{_RANK1_ISIN}", isin=_RANK1_ISIN,
        units=Decimal("10"), nav=Decimal("60"), txn_date=date(2024, 1, 1),
    )
    elss_isin = "INF000ELSS001"
    await _add_holding(
        db_session, user=user, scheme_code="ELSS_SCHEME_001", isin=elss_isin,
        units=Decimal("5"), nav=Decimal("80"), txn_date=date(2022, 1, 1),
        asset_subgroup="tax_efficient_equities", sub_category="ELSS Tax Saver Fund",
    )

    request, _ = await build_rebalancing_input_for_user(
        _ctx_for(user, db_session), fixture_goal_allocation_output_one_subgroup,
    )

    assert not [r for r in request.rows if r.asset_subgroup == "tax_efficient_equities"]
    assert request.practical_allocation_input.elss_corpus == pytest.approx(400.0)
    assert request.total_corpus == Decimal("1000")

    response = run_rebalancing(request)
    traded = {
        a.isin
        for s in response.subgroups
        for a in s.actions
        if a.pass1_buy_amount > 0 or a.pass1_sell_amount + a.pass2_sell_amount > 0
    }
    assert elss_isin not in traded
    frozen = [s for s in response.subgroups if s.asset_subgroup == "tax_efficient_equities"]
    assert len(frozen) == 1 and frozen[0].current_holding_inr == Decimal("400")
