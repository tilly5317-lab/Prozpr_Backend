"""POST /additional-investment/{run_id}/save-preference, over sqlite."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport
from sqlalchemy import ARRAY, JSON
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

import app.all_models  # noqa: F401  -- registers every model with Base.metadata
from app.core.database import Base, get_db
from app.core.dependencies import get_ai_user_context, get_effective_user
from app.domains.additional_investment.models.additional_investment_run import (
    AdditionalInvestmentRun,
    Cadence,
    TargetBucket,
)
from app.domains.additional_investment.routers.additional_investment_router import (
    router,
)

# Same trick as rebalancing's test_saved_plan_routes.py: swap every Postgres
# ARRAY column to JSON so Base.metadata.create_all works on sqlite.
for _table in Base.metadata.tables.values():
    for _column in _table.columns:
        if isinstance(_column.type, ARRAY):
            _column.type = JSON()

T0 = datetime(2026, 8, 1, tzinfo=timezone.utc)
# Hex letters (a-f) are load-bearing: sqlite gives a ``UUID``-typed column
# NUMERIC affinity, so an all-digit UUID hex is coerced to a float on store and
# crashes on read. Prod (Postgres native uuid) is unaffected.
USER_ID = uuid.UUID("a1a1a1a1-a1a1-4a1a-8a1a-a1a1a1a1a1a1")
OTHER_USER_ID = uuid.UUID("b2b2b2b2-b2b2-4b2b-8b2b-b2b2b2b2b2b2")


def _run(**overrides) -> AdditionalInvestmentRun:
    kwargs = dict(
        user_id=USER_ID,
        portfolio_id=uuid.uuid4(),
        source_allocation_run_id=uuid.uuid4(),
        engine_version="ainv-test",
        target_bucket=TargetBucket.LONG_TERM,
        cadence=Cadence.SIP_MONTHLY,
        deploy_amount_inr=10_000,
        deployed_inr=10_000,
        undeployed_inr=0,
        created_at=T0,
    )
    kwargs.update(overrides)
    return AdditionalInvestmentRun(**kwargs)


@pytest_asyncio.fixture
async def app_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    session = factory()
    application = FastAPI()
    application.include_router(router)

    async def _get_db():
        yield session

    application.dependency_overrides[get_db] = _get_db
    application.dependency_overrides[get_effective_user] = lambda: SimpleNamespace(
        id=USER_ID
    )
    application.dependency_overrides[get_ai_user_context] = lambda: SimpleNamespace(
        id=USER_ID
    )
    try:
        yield application, session
    finally:
        await session.rollback()
        await session.close()
        await engine.dispose()


async def _client(application):
    # raise_app_exceptions=False: an unhandled exception in the endpoint (the
    # activation-raises test below) must surface as a real 500 response
    # rather than propagate out of the client call.
    return httpx.AsyncClient(
        transport=ASGITransport(app=application, raise_app_exceptions=False),
        base_url="http://t",
    )


async def test_save_preference_activates_candidate(app_session, monkeypatch):
    application, session = app_session
    from app.domains.profile.models.saved_investment_preference import (
        SavedInvestmentPreference,
    )
    from app.domains.profile.services import preference_save_service

    row = SavedInvestmentPreference(user_id=USER_ID, is_active=False)
    session.add(row)
    await session.flush()
    r = _run(saved_investment_preference_id=row.id)
    session.add(r)
    await session.commit()

    calls = []

    async def _fake_confirm(db, user, candidate):
        calls.append(candidate.id)
        return SimpleNamespace(no_op=False)

    monkeypatch.setattr(preference_save_service, "confirm_candidate", _fake_confirm)

    async with await _client(application) as ac:
        resp = await ac.post(f"/additional-investment/{r.id}/save-preference")

    assert resp.status_code == 200
    assert resp.json() == {"activated": True}
    assert calls == [row.id]


async def test_save_preference_without_candidate_touches_no_preference(
    app_session, monkeypatch
):
    application, session = app_session
    from app.domains.profile.services import preference_save_service

    r = _run()
    session.add(r)
    await session.commit()

    calls = []

    async def _fake_confirm(db, user, candidate):
        calls.append(candidate.id)
        return SimpleNamespace(no_op=False)

    monkeypatch.setattr(preference_save_service, "confirm_candidate", _fake_confirm)

    async with await _client(application) as ac:
        resp = await ac.post(f"/additional-investment/{r.id}/save-preference")

    assert resp.status_code == 200
    assert resp.json() == {"activated": False}
    assert calls == []


async def test_save_preference_unknown_run_404(app_session):
    application, _ = app_session
    async with await _client(application) as ac:
        resp = await ac.post(f"/additional-investment/{uuid.uuid4()}/save-preference")
    assert resp.status_code == 404


async def test_save_preference_other_users_run_404(app_session):
    application, session = app_session
    r = _run(user_id=OTHER_USER_ID)
    session.add(r)
    await session.commit()

    async with await _client(application) as ac:
        resp = await ac.post(f"/additional-investment/{r.id}/save-preference")
    assert resp.status_code == 404


async def test_save_preference_scopes_lookup_to_user_ctx_id(app_session, monkeypatch):
    """Split the two seams: user_ctx (get_ai_user_context) resolves to
    USER_ID, the effective user (get_effective_user) resolves to a DIFFERENT
    id (OTHER_USER_ID). Today's real dependency graph can't produce this
    (get_ai_user_context derives from get_effective_user), but overriding
    them independently is how the test proves the route's run lookup keys
    off user_ctx.id -- the same id the activation uses -- not current_user.id.
    A run owned by USER_ID must still be found and activated."""
    application, session = app_session
    from app.domains.profile.models.saved_investment_preference import (
        SavedInvestmentPreference,
    )
    from app.domains.profile.services import preference_save_service

    application.dependency_overrides[get_effective_user] = lambda: SimpleNamespace(
        id=OTHER_USER_ID
    )

    row = SavedInvestmentPreference(user_id=USER_ID, is_active=False)
    session.add(row)
    await session.flush()
    r = _run(user_id=USER_ID, saved_investment_preference_id=row.id)
    session.add(r)
    await session.commit()

    calls = []

    async def _fake_confirm(db, user, candidate):
        calls.append(candidate.id)
        return SimpleNamespace(no_op=False)

    monkeypatch.setattr(preference_save_service, "confirm_candidate", _fake_confirm)

    async with await _client(application) as ac:
        resp = await ac.post(f"/additional-investment/{r.id}/save-preference")

    assert resp.status_code == 200
    assert resp.json() == {"activated": True}
    assert calls == [row.id]


async def test_save_preference_run_owned_by_effective_user_id_still_404s(
    app_session,
):
    """Mirror of the test above: a run owned by the effective-user id
    (OTHER_USER_ID), not user_ctx's id (USER_ID), must 404 -- proving the
    lookup keys off user_ctx.id specifically, not either resolved id."""
    application, session = app_session
    application.dependency_overrides[get_effective_user] = lambda: SimpleNamespace(
        id=OTHER_USER_ID
    )

    r = _run(user_id=OTHER_USER_ID)
    session.add(r)
    await session.commit()

    async with await _client(application) as ac:
        resp = await ac.post(f"/additional-investment/{r.id}/save-preference")
    assert resp.status_code == 404


async def test_save_preference_activation_raising_is_a_500(app_session, monkeypatch):
    """Unlike the rebalancing 'save' route, there is no prior commit to
    protect here — a failure must surface as a 500, not a false 200."""
    application, session = app_session
    from app.domains.profile.models.saved_investment_preference import (
        SavedInvestmentPreference,
    )
    from app.domains.profile.services import preference_save_service

    row = SavedInvestmentPreference(user_id=USER_ID, is_active=False)
    session.add(row)
    await session.flush()
    r = _run(saved_investment_preference_id=row.id)
    session.add(r)
    await session.commit()

    calls = []

    async def _raising_confirm(db, user, candidate):
        calls.append(candidate.id)
        raise RuntimeError("boom")

    monkeypatch.setattr(preference_save_service, "confirm_candidate", _raising_confirm)

    async with await _client(application) as ac:
        resp = await ac.post(f"/additional-investment/{r.id}/save-preference")

    assert resp.status_code == 500
    assert calls == [row.id]
