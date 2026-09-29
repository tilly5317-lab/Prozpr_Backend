"""Async DB fixtures for visualization_tools tests.

Mirrors the fixture set in ``app/services/ai_bridge/rebalancing/tests/conftest.py``
(in-memory SQLite, full ``Base.metadata`` schema, per-test isolation). Pytest
does not auto-share fixtures across sibling test directories, so we duplicate
the minimal subset needed by chart-builder tests rather than promote them to
a project-level conftest as part of this work.
"""
from __future__ import annotations

import uuid
from datetime import date
from typing import AsyncIterator

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Side-effect imports: register every model with Base.metadata so create_all
# materialises the entire schema (FK targets must exist before children).
import app.models  # noqa: F401
from app.database import Base
from app.models.user import User


@pytest_asyncio.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    """Per-test in-memory SQLite session; engine disposed at teardown."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        try:
            yield session
        finally:
            await session.rollback()
    await engine.dispose()


@pytest_asyncio.fixture
async def fixture_user(db_session: AsyncSession) -> User:
    suffix = uuid.uuid4().hex[:8]
    user = User(
        id=uuid.uuid4(),
        email=f"viz_test_{suffix}@example.com",
        country_code="+91",
        mobile="9999999999",
        phone=f"+91-9999{suffix}",
    )
    db_session.add(user)
    await db_session.flush()
    return user


@pytest_asyncio.fixture
async def fixture_user_with_dob(db_session: AsyncSession) -> User:
    """User with a date_of_birth (some downstream code expects it)."""
    suffix = uuid.uuid4().hex[:8]
    user = User(
        id=uuid.uuid4(),
        email=f"viz_dob_{suffix}@example.com",
        country_code="+91",
        mobile="9999999999",
        phone=f"+91-9999{suffix}",
        date_of_birth=date(1990, 1, 1),
    )
    db_session.add(user)
    await db_session.flush()
    return user


# ── Portfolio fixtures (used by current_donut, target_vs_actual) ──
import uuid as _uuid
from decimal import Decimal

from app.models.portfolio import Portfolio, PortfolioAllocation


@pytest_asyncio.fixture
async def fixture_user_with_portfolio_and_allocations(
    db_session, fixture_user_with_dob,
):
    """Adds a Portfolio + 3 PortfolioAllocation rows (Equity/Debt/Cash) summing to 100%."""
    portfolio = Portfolio(
        id=_uuid.uuid4(),
        user_id=fixture_user_with_dob.id,
        is_primary=True,
    )
    db_session.add(portfolio)
    await db_session.flush()
    for cls, amount, pct in (
        ("Equity", Decimal("700000"), Decimal("70.00")),
        ("Debt", Decimal("250000"), Decimal("25.00")),
        ("Cash", Decimal("50000"), Decimal("5.00")),
    ):
        db_session.add(PortfolioAllocation(
            id=_uuid.uuid4(),
            portfolio_id=portfolio.id,
            asset_class=cls,
            amount=amount,
            allocation_percentage=pct,
        ))
    await db_session.flush()
    return fixture_user_with_dob
