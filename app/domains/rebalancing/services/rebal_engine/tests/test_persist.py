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


@pytest.mark.asyncio
async def test_persist_tags_preference_fk_when_override_applied(
    db_session,
    fixture_user_with_dob,
    fixture_rebalancing_response,
    fixture_allocation_row,
):
    """A run computed with a preference must carry the FK of the active
    preference row — derived from the RESPONSE (practical_allocation.
    human_override_applied), so every caller is covered by construction."""
    from practical_asset_allocation.human_override import HumanOverrideApplied

    from app.domains.profile.models.saved_investment_preference import (
        SavedInvestmentPreference,
    )
    from app.domains.rebalancing.services.rebalancing_persist_service import (
        persist_rebalancing_recommendation,
    )

    pref = SavedInvestmentPreference(user_id=fixture_user_with_dob.id)
    db_session.add(pref)
    await db_session.flush()

    response = fixture_rebalancing_response.model_copy(update={
        "practical_allocation": fixture_rebalancing_response.practical_allocation.model_copy(
            update={
                # Its presence IS the "this run used a preference" signal.
                "human_override_applied": HumanOverrideApplied()
            }
        )
    })

    run_id = await persist_rebalancing_recommendation(
        db_session,
        fixture_user_with_dob.id,
        response,
        source_allocation_run_id=fixture_allocation_row.id,
        chat_session_id=None,
    )

    from sqlalchemy import select

    from app.domains.rebalancing.models.rebalancing_run import RebalancingRun

    run = (
        await db_session.execute(
            select(RebalancingRun).where(RebalancingRun.id == run_id)
        )
    ).scalar_one()
    assert run.saved_investment_preference_id == pref.id


@pytest.mark.asyncio
async def test_persist_stores_explicit_preference_fk_verbatim(
    db_session, fixture_user_with_dob, fixture_rebalancing_response, fixture_allocation_row,
):
    from sqlalchemy import select

    from app.domains.profile.models.saved_investment_preference import (
        SavedInvestmentPreference,
    )
    from app.domains.rebalancing.models.rebalancing_run import RebalancingRun
    from app.domains.rebalancing.services.rebalancing_persist_service import (
        persist_rebalancing_recommendation,
    )

    candidate = SavedInvestmentPreference(user_id=fixture_user_with_dob.id, is_active=False)
    db_session.add(candidate)
    await db_session.flush()

    run_id = await persist_rebalancing_recommendation(
        db_session, fixture_user_with_dob.id, fixture_rebalancing_response,
        source_allocation_run_id=fixture_allocation_row.id, chat_session_id=None,
        used_cached_allocation=True, user_question="what if 100% equity",
        saved_investment_preference_id=candidate.id,
    )
    run = (await db_session.execute(select(RebalancingRun).where(RebalancingRun.id == run_id))).scalar_one()
    assert run.saved_investment_preference_id == candidate.id


@pytest.mark.asyncio
async def test_persist_explicit_none_never_borrows_the_active_row(
    db_session, fixture_user_with_dob, fixture_rebalancing_response, fixture_allocation_row,
):
    """A one-off shaped this run (human_override_applied is set) while the
    customer also has an active saved row: the DERIVE path would tag the saved
    row, so an explicit None must win."""
    from practical_asset_allocation.human_override import HumanOverrideApplied
    from sqlalchemy import select

    from app.domains.profile.models.saved_investment_preference import (
        SavedInvestmentPreference,
    )
    from app.domains.rebalancing.models.rebalancing_run import RebalancingRun
    from app.domains.rebalancing.services.rebalancing_persist_service import (
        persist_rebalancing_recommendation,
    )

    db_session.add(SavedInvestmentPreference(user_id=fixture_user_with_dob.id))  # active
    await db_session.flush()
    response = fixture_rebalancing_response.model_copy(update={
        "practical_allocation": fixture_rebalancing_response.practical_allocation.model_copy(
            update={
                # Its presence IS the "this run used a preference" signal.
                "human_override_applied": HumanOverrideApplied()
            }
        )
    })

    run_id = await persist_rebalancing_recommendation(
        db_session, fixture_user_with_dob.id, response,
        source_allocation_run_id=fixture_allocation_row.id, chat_session_id=None,
        used_cached_allocation=True, user_question="what if",
        saved_investment_preference_id=None,
    )
    run = (await db_session.execute(select(RebalancingRun).where(RebalancingRun.id == run_id))).scalar_one()
    assert run.saved_investment_preference_id is None
