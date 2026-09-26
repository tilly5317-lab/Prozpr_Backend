"""Session-scoped "current additional-investment run" over real sqlite.

``get_session_current_ainv`` returns ``(cadence, save_preference_run_id)`` for
the session's most recent run — the two datums the chat restores its pills from
on reload:

* ``cadence`` restores "View plan" for ANY deploy (scoping to the session keeps
  a SIP turn from restoring a lump-sum popup).
* ``save_preference_run_id`` restores "Save preference", set ONLY when that
  latest run carries an UNSAVED what-if candidate (``activated_at`` NULL) — an
  ordinary deploy, an already-saved candidate, or a later ordinary deploy that
  supersedes an earlier what-if all leave it None (nothing left to save).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest_asyncio
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

import app.all_models  # noqa: F401  -- registers FK target tables with Base.metadata
from app.domains.additional_investment.models import (
    AdditionalInvestmentRun,
    Cadence,
    TargetBucket,
)
from app.domains.additional_investment.services.additional_investment_read_service import (
    get_session_current_ainv,
)
from app.domains.profile.models.saved_investment_preference import (
    SavedInvestmentPreference,
)

T0 = datetime(2026, 9, 1, tzinfo=timezone.utc)


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        # Base.metadata.create_all FAILS on sqlite (unrelated Postgres ARRAY
        # model) — create only the two tables under test (the run + the
        # candidate preference its "Save preference" state joins to).
        await conn.run_sync(SavedInvestmentPreference.__table__.create)
        await conn.run_sync(AdditionalInvestmentRun.__table__.create)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        try:
            yield session
        finally:
            await session.rollback()
    await engine.dispose()


def _pref(user_id, *, activated_at) -> SavedInvestmentPreference:
    """A candidate preference. ``activated_at`` NULL = an unsaved what-if
    candidate (inactive); a datetime = the customer saved it (active)."""
    return SavedInvestmentPreference(
        user_id=user_id,
        is_active=activated_at is not None,
        activated_at=activated_at,
    )


def _run(
    user_id, session_id, created_at, *, cadence=Cadence.LUMPSUM, pref_id=None
) -> AdditionalInvestmentRun:
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
        chat_session_id=session_id,
        saved_investment_preference_id=pref_id,
        created_at=created_at,
    )


# ── cadence (restores "View plan") ──────────────────────────────────────────


async def test_cadence_is_the_latest_in_session(db_session):
    user, sid = uuid.uuid4(), uuid.uuid4()
    db_session.add(_run(user, sid, T0, cadence=Cadence.SIP_MONTHLY))
    db_session.add(_run(user, sid, T0 + timedelta(hours=1), cadence=Cadence.LUMPSUM))
    await db_session.flush()
    cadence, _ = await get_session_current_ainv(db_session, user, sid)
    assert cadence == "lumpsum"


async def test_both_none_when_session_has_no_runs(db_session):
    assert await get_session_current_ainv(
        db_session, uuid.uuid4(), uuid.uuid4()
    ) == (None, None)


async def test_cadence_scoped_to_the_session(db_session):
    """A newer run in ANOTHER session must not leak in — this is what keeps a
    SIP turn from restoring a lump-sum popup."""
    user, sid_a, sid_b = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    db_session.add(_run(user, sid_a, T0, cadence=Cadence.SIP_MONTHLY))
    db_session.add(_run(user, sid_b, T0 + timedelta(hours=1), cadence=Cadence.LUMPSUM))
    await db_session.flush()
    cadence, _ = await get_session_current_ainv(db_session, user, sid_a)
    assert cadence == "sip_monthly"


# ── save_preference_run_id (restores "Save preference") ──────────────────────


async def test_save_run_id_is_the_latest_unsaved_whatif(db_session):
    user, sid = uuid.uuid4(), uuid.uuid4()
    cand = _pref(user, activated_at=None)
    db_session.add(cand)
    await db_session.flush()
    run = _run(user, sid, T0, pref_id=cand.id)
    db_session.add(run)
    await db_session.flush()
    _, save_run_id = await get_session_current_ainv(db_session, user, sid)
    assert save_run_id == run.id


async def test_save_run_id_none_when_candidate_already_saved(db_session):
    """A candidate the customer activated has nothing left to save."""
    user, sid = uuid.uuid4(), uuid.uuid4()
    saved = _pref(user, activated_at=T0)
    db_session.add(saved)
    await db_session.flush()
    db_session.add(_run(user, sid, T0, pref_id=saved.id))
    await db_session.flush()
    _, save_run_id = await get_session_current_ainv(db_session, user, sid)
    assert save_run_id is None


async def test_save_run_id_none_when_run_has_no_candidate(db_session):
    """An ordinary deploy carries no preference candidate."""
    user, sid = uuid.uuid4(), uuid.uuid4()
    db_session.add(_run(user, sid, T0))
    await db_session.flush()
    _, save_run_id = await get_session_current_ainv(db_session, user, sid)
    assert save_run_id is None


async def test_save_run_id_none_when_later_ordinary_deploy_supersedes_whatif(db_session):
    """The pill follows the LATEST run: an ordinary deploy after a what-if has
    no candidate, so the earlier what-if is not resurfaced on restore."""
    user, sid = uuid.uuid4(), uuid.uuid4()
    cand = _pref(user, activated_at=None)
    db_session.add(cand)
    await db_session.flush()
    db_session.add(_run(user, sid, T0, pref_id=cand.id))  # what-if
    db_session.add(_run(user, sid, T0 + timedelta(hours=1)))  # later ordinary deploy
    await db_session.flush()
    _, save_run_id = await get_session_current_ainv(db_session, user, sid)
    assert save_run_id is None


async def test_save_run_id_scoped_to_the_session(db_session):
    """A pending candidate in ANOTHER session must not leak in."""
    user, sid_a, sid_b = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    cand = _pref(user, activated_at=None)
    db_session.add(cand)
    await db_session.flush()
    run_a = _run(user, sid_a, T0, pref_id=cand.id)
    db_session.add(run_a)
    await db_session.flush()
    assert (await get_session_current_ainv(db_session, user, sid_b))[1] is None
    assert (await get_session_current_ainv(db_session, user, sid_a))[1] == run_a.id
