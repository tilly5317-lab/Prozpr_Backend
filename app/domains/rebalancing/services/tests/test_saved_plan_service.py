"""save_plan / select_current_run_id + the origin column (spec 2026-08-27)."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

import app.all_models  # noqa: F401  -- registers FK target tables with Base.metadata
from app.domains.rebalancing.models.rebalancing_run import RebalancingRun, TaxRegime

T0 = datetime(2026, 8, 1, tzinfo=timezone.utc)


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        # Base.metadata.create_all FAILS on sqlite (unrelated Postgres ARRAY) —
        # create only the table under test.
        await conn.run_sync(RebalancingRun.__table__.create)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        try:
            yield session
        finally:
            await session.rollback()
    await engine.dispose()


def _run(user_id: uuid.UUID, when: datetime, **overrides) -> RebalancingRun:
    kwargs = dict(
        user_id=user_id,
        portfolio_id=uuid.uuid4(),
        source_allocation_run_id=uuid.uuid4(),
        engine_request_id=uuid.uuid4(),
        engine_version="rebal-test",
        computed_at=when,
        tax_regime=TaxRegime.new,
        effective_tax_rate_pct=30,
        total_corpus=1_000_000,
        created_at=when,  # set explicitly; func.now() server_default isn't portable to sqlite
    )
    kwargs.update(overrides)
    return RebalancingRun(**kwargs)


async def test_origin_column_defaults_none_and_is_settable(db_session):
    run = _run(uuid.uuid4(), T0)
    db_session.add(run)
    await db_session.flush()
    assert run.origin is None
    run.origin = "saved"
    await db_session.flush()
    got = (
        await db_session.execute(
            select(RebalancingRun).where(RebalancingRun.id == run.id)
        )
    ).scalar_one()
    assert got.origin == "saved"


from app.domains.rebalancing.services.saved_plan_service import save_plan


async def test_save_plan_marks_run_saved(db_session):
    uid = uuid.uuid4()
    r = _run(uid, T0)
    db_session.add(r)
    await db_session.flush()
    got = await save_plan(db_session, user_id=uid, run_id=r.id)
    assert got is not None and got.origin == "saved"


async def test_save_plan_demotes_prior_saved(db_session):
    uid = uuid.uuid4()
    old = _run(uid, T0, origin="saved")
    new = _run(uid, T0)
    db_session.add_all([old, new])
    await db_session.flush()
    await save_plan(db_session, user_id=uid, run_id=new.id)
    await db_session.refresh(old)
    await db_session.refresh(new)
    assert new.origin == "saved"
    assert old.origin is None


async def test_save_plan_is_idempotent(db_session):
    uid = uuid.uuid4()
    r = _run(uid, T0)
    db_session.add(r)
    await db_session.flush()
    await save_plan(db_session, user_id=uid, run_id=r.id)
    got = await save_plan(db_session, user_id=uid, run_id=r.id)
    assert got is not None and got.origin == "saved"


async def test_save_plan_returns_none_for_other_user(db_session):
    r = _run(uuid.uuid4(), T0)
    db_session.add(r)
    await db_session.flush()
    got = await save_plan(db_session, user_id=uuid.uuid4(), run_id=r.id)
    assert got is None


from app.domains.rebalancing.services.saved_plan_service import select_current_run_id


async def test_select_current_none_when_empty(db_session):
    assert await select_current_run_id(db_session, user_id=uuid.uuid4()) is None


async def test_select_current_returns_only_run(db_session):
    uid = uuid.uuid4()
    r = _run(uid, T0)
    db_session.add(r)
    await db_session.flush()
    assert await select_current_run_id(db_session, user_id=uid) == r.id


async def test_select_current_prefers_saved_over_newer_plain(db_session):
    uid = uuid.uuid4()
    saved = _run(uid, T0, origin="saved")
    newer = _run(uid, T0 + timedelta(days=1))  # newer compute, but not saved
    db_session.add_all([saved, newer])
    await db_session.flush()
    assert await select_current_run_id(db_session, user_id=uid) == saved.id


async def test_select_current_latest_when_none_saved(db_session):
    uid = uuid.uuid4()
    older = _run(uid, T0)
    newer = _run(uid, T0 + timedelta(days=1))
    db_session.add_all([older, newer])
    await db_session.flush()
    assert await select_current_run_id(db_session, user_id=uid) == newer.id


# ── tilt-save: candidate firewall (2026-08-30) ──────────────────────────────


async def test_select_current_excludes_unsaved_candidate(db_session):
    uid = uuid.uuid4()
    plain = _run(uid, T0)
    # a newer tilt the customer viewed but did NOT save
    candidate = _run(uid, T0 + timedelta(days=1), origin="candidate")
    db_session.add_all([plain, candidate])
    await db_session.flush()
    # the page shows the committed plain run, never the un-saved candidate
    assert await select_current_run_id(db_session, user_id=uid) == plain.id


async def test_select_current_returns_saved_tilt(db_session):
    uid = uuid.uuid4()
    plain = _run(uid, T0)
    saved_tilt = _run(uid, T0 + timedelta(days=1), origin="saved")
    db_session.add_all([plain, saved_tilt])
    await db_session.flush()
    assert await select_current_run_id(db_session, user_id=uid) == saved_tilt.id


async def test_save_plan_flips_candidate_to_saved_and_becomes_current(db_session):
    uid = uuid.uuid4()
    plain = _run(uid, T0)
    candidate = _run(uid, T0 + timedelta(days=1), origin="candidate")
    db_session.add_all([plain, candidate])
    await db_session.flush()
    # before saving, the candidate is firewalled → page shows the plain run
    assert await select_current_run_id(db_session, user_id=uid) == plain.id
    got = await save_plan(db_session, user_id=uid, run_id=candidate.id)
    assert got is not None and got.origin == "saved"
    # after saving, the (now-saved) tilt is the current run
    assert await select_current_run_id(db_session, user_id=uid) == candidate.id
