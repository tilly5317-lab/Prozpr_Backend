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
