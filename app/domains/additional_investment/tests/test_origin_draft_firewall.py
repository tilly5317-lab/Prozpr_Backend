"""Draft firewall: an unsaved what-if (origin='candidate') run must not leak into
the Invest-page reads, but must stay viewable by its run-id (the chat "View plan"
popup). Mirrors rebalancing's origin mechanism.

* ``_committed_run_filter()`` excludes only 'candidate'; NULL (plain deploy) and
  any other origin are committed. The explicit ``is_(None)`` branch is
  load-bearing — in SQL ``origin != 'candidate'`` is NULL (excluded) for a
  NULL-origin row, which would hide every plain run.
* ``get_ainv_plan_for_run`` is origin-agnostic — it returns a candidate draft by
  id so the popup can show the what-if the customer is viewing.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

import app.all_models  # noqa: F401  -- registers FK target tables with Base.metadata
from app.domains.additional_investment.models import (
    AdditionalInvestmentBuy,
    AdditionalInvestmentRun,
    Cadence,
    TargetBucket,
)
from app.domains.additional_investment.schemas import (
    LumpsumPlanResponse,
    SipPlanResponse,
)
from app.domains.additional_investment.services.additional_investment_read_service import (
    ORIGIN_CANDIDATE,
    _committed_run_filter,
    get_ainv_plan_for_run,
)

T0 = datetime(2026, 9, 1, tzinfo=timezone.utc)


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(AdditionalInvestmentRun.__table__.create)
        await conn.run_sync(AdditionalInvestmentBuy.__table__.create)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        try:
            yield session
        finally:
            await session.rollback()
    await engine.dispose()


def _run(user_id, *, cadence=Cadence.SIP_MONTHLY, origin=None, created_at=T0):
    return AdditionalInvestmentRun(
        user_id=user_id,
        portfolio_id=uuid.uuid4(),
        source_allocation_run_id=uuid.uuid4(),
        engine_version="ainv-test",
        target_bucket=TargetBucket.LONG_TERM,
        cadence=cadence,
        deploy_amount_inr=100000.0,
        deployed_inr=100000.0,
        undeployed_inr=0.0,
        origin=origin,
        created_at=created_at,
    )


def _buy(run_id, *, subgroup="medium_beta_equities", monthly=None):
    return AdditionalInvestmentBuy(
        run_id=run_id,
        recommended_fund="Test Fund - Direct Growth",
        isin="INF000TEST01",
        sub_category="Mid Cap Fund",
        asset_subgroup=subgroup,
        rank=1,
        scheme_code="12345",
        amount_inr=100000.0,
        monthly_amount_inr=monthly,
        reason="",
    )


# ── the committed filter ─────────────────────────────────────────────────────


async def test_committed_filter_excludes_only_candidate(db_session):
    user = uuid.uuid4()
    plain = _run(user, origin=None)
    candidate = _run(user, origin=ORIGIN_CANDIDATE)
    other = _run(user, origin="saved")
    db_session.add_all([plain, candidate, other])
    await db_session.flush()

    ids = set(
        (
            await db_session.execute(
                select(AdditionalInvestmentRun.id).where(_committed_run_filter())
            )
        ).scalars()
    )
    # NULL (plain) and 'saved' are committed; only 'candidate' is firewalled out.
    assert ids == {plain.id, other.id}


# ── by-run-id fetch (origin-agnostic) ────────────────────────────────────────


async def test_get_ainv_plan_for_run_returns_candidate_sip_by_id(db_session):
    """A candidate draft is hidden from the committed reads but still fully
    viewable by its run-id — this is how the chat 'View plan' popup shows it."""
    user = uuid.uuid4()
    run = _run(user, cadence=Cadence.SIP_MONTHLY, origin=ORIGIN_CANDIDATE)
    db_session.add(run)
    await db_session.flush()
    db_session.add(_buy(run.id, monthly=100000.0))
    await db_session.flush()

    plan = await get_ainv_plan_for_run(db_session, user, run.id)
    assert isinstance(plan, SipPlanResponse)
    assert plan.has_plan is True
    assert plan.run_id == run.id
    assert plan.fund_count == 1


async def test_get_ainv_plan_for_run_picks_lumpsum_shape(db_session):
    user = uuid.uuid4()
    run = _run(user, cadence=Cadence.LUMPSUM, origin=ORIGIN_CANDIDATE)
    db_session.add(run)
    await db_session.flush()
    db_session.add(_buy(run.id))
    await db_session.flush()

    plan = await get_ainv_plan_for_run(db_session, user, run.id)
    assert isinstance(plan, LumpsumPlanResponse)
    assert plan.amount_inr == 100000.0


async def test_get_ainv_plan_for_run_none_for_missing_or_other_user(db_session):
    user = uuid.uuid4()
    run = _run(user)
    db_session.add(run)
    await db_session.flush()

    assert await get_ainv_plan_for_run(db_session, user, uuid.uuid4()) is None
    assert await get_ainv_plan_for_run(db_session, uuid.uuid4(), run.id) is None
