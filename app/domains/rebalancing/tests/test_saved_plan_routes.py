"""POST /rebalancing/{id}/save + GET /rebalancing/current, over sqlite."""

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
from app.core.dependencies import get_effective_user
from app.domains.rebalancing.models.rebalancing_run import RebalancingRun, TaxRegime
from app.domains.rebalancing.routers.rebalancing_router import router

# get_current eager-loads rebalancing_warnings, whose affected_isins is a
# Postgres ARRAY — uncreatable on sqlite. Swap every ARRAY column to JSON for
# this test process so Base.metadata.create_all materialises the whole schema
# (same trick as rebal_engine/tests/conftest.py; test-only, never loaded in prod).
for _table in Base.metadata.tables.values():
    for _column in _table.columns:
        if isinstance(_column.type, ARRAY):
            _column.type = JSON()

T0 = datetime(2026, 8, 1, tzinfo=timezone.utc)
T1 = datetime(2026, 8, 2, tzinfo=timezone.utc)
# Hex letters (a-f) are load-bearing: sqlite gives a ``UUID``-typed column NUMERIC
# affinity, so an all-digit UUID hex (e.g. 1111…) is coerced to a float on store
# and read back as one, crashing the Uuid result processor. Prod (Postgres native
# uuid) is unaffected; this only bites the sqlite test backend.
USER_ID = uuid.UUID("a1a1a1a1-a1a1-4a1a-8a1a-a1a1a1a1a1a1")


def _run(when: datetime, **overrides) -> RebalancingRun:
    kwargs = dict(
        user_id=USER_ID,
        portfolio_id=uuid.uuid4(),
        source_allocation_run_id=uuid.uuid4(),
        engine_request_id=uuid.uuid4(),
        engine_version="rebal-test",
        computed_at=when,
        tax_regime=TaxRegime.new,
        effective_tax_rate_pct=30,
        total_corpus=1_000_000,
        created_at=when,
    )
    kwargs.update(overrides)
    return RebalancingRun(**kwargs)


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
    try:
        yield application, session
    finally:
        await session.rollback()
        await session.close()
        await engine.dispose()


async def _client(application):
    return httpx.AsyncClient(
        transport=ASGITransport(app=application), base_url="http://t"
    )


async def test_save_then_current_returns_saved_run(app_session):
    application, session = app_session
    r = _run(T0)
    session.add(r)
    await session.commit()
    async with await _client(application) as ac:
        saved = await ac.post(f"/rebalancing/{r.id}/save")
        assert saved.status_code == 200
        assert saved.json()["origin"] == "saved"

        current = await ac.get("/rebalancing/current")
        assert current.status_code == 200
        assert current.json()["id"] == str(r.id)
        assert current.json()["origin"] == "saved"  # detail schema serializes origin


async def test_save_unknown_run_404(app_session):
    application, _ = app_session
    async with await _client(application) as ac:
        resp = await ac.post(f"/rebalancing/{uuid.uuid4()}/save")
        assert resp.status_code == 404


async def test_current_404_when_no_runs(app_session):
    application, _ = app_session
    async with await _client(application) as ac:
        resp = await ac.get("/rebalancing/current")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Task 12 fix round 1: candidate->commit firewall — a committed ('saved')
# plan is intentionally sticky and must never be replaced by the freshness
# backstop; only the uncommitted-current view self-heals.
# ---------------------------------------------------------------------------


async def _make_aa_runs(session):
    """Two allocation runs for USER_ID, T0 (older) and T1 (newer)."""
    from app.domains.asset_allocation.models.run import AssetAllocationRun

    older = AssetAllocationRun(
        user_id=USER_ID,
        client_age=35,
        client_effective_risk_score=50,
        total_corpus=1_000_000,
        grand_total=1_000_000,
        created_at=T0,
    )
    newer = AssetAllocationRun(
        user_id=USER_ID,
        client_age=35,
        client_effective_risk_score=50,
        total_corpus=1_000_000,
        grand_total=1_000_000,
        created_at=T1,
    )
    session.add_all([older, newer])
    await session.flush()
    return older, newer


async def test_stale_saved_plan_is_served_unchanged_and_not_refreshed(
    app_session, monkeypatch
):
    """F1/F3(a): a stale ``origin='saved'`` plan (points at the OLDER of two
    allocation runs) must be served as-is — the freshness backstop must not
    even be invoked. A committed plan is intentionally sticky; re-saving is
    the customer's action, not this read's."""
    application, session = app_session
    older, _newer = await _make_aa_runs(session)
    r = _run(T0, origin="saved", source_allocation_run_id=older.id)
    session.add(r)
    await session.commit()

    from app.domains.rebalancing.routers import rebalancing_router as router_mod

    calls = {"n": 0}

    async def _spy(*a, **k):
        calls["n"] += 1
        return None

    monkeypatch.setattr(router_mod, "_refresh_stale_current", _spy)

    async with await _client(application) as ac:
        current = await ac.get("/rebalancing/current")
        assert current.status_code == 200
        assert current.json()["id"] == str(r.id)
        assert current.json()["origin"] == "saved"

    assert calls["n"] == 0, (
        "a committed saved plan must never trigger the freshness backstop, "
        "even when it is stale"
    )


async def test_stale_uncommitted_current_triggers_single_recompute(
    app_session, monkeypatch
):
    """F3(b): a stale, un-committed (``origin=None``) current run (points at
    the OLDER of two allocation runs) triggers exactly ONE recompute, and the
    response serves the re-selected (freshly recomputed) run."""
    application, session = app_session
    from app.domains.identity.models.user import User

    older, newer = await _make_aa_runs(session)
    session.add(
        User(id=USER_ID, country_code="+91", mobile="9999999999", phone="+91-9999999999")
    )
    r = _run(T0, source_allocation_run_id=older.id)  # origin=None (plain, uncommitted)
    session.add(r)
    await session.commit()

    calls = {"n": 0}

    async def _fake_compute(user_ctx, question, *, db, acting_user_id, **kwargs):
        calls["n"] += 1
        fresh = _run(T1, source_allocation_run_id=newer.id, user_id=acting_user_id)
        db.add(fresh)
        await db.flush()
        return None

    monkeypatch.setattr(
        "app.domains.rebalancing.services.rebal_engine.service.compute_rebalancing_result",
        _fake_compute,
    )

    async with await _client(application) as ac:
        current = await ac.get("/rebalancing/current")
        assert current.status_code == 200
        assert current.json()["id"] != str(r.id), (
            "must serve the re-selected, freshly recomputed run"
        )
        assert current.json()["source_allocation_run_id"] == str(newer.id)

    assert calls["n"] == 1, "exactly one recompute, no loop"
