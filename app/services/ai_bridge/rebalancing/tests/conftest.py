"""Async DB fixtures for rebalancing tests.

The repo has no shared async-DB fixture infrastructure (existing tests under
``app/services/ai_bridge/`` rely on mocks), so this conftest stands up a
fully isolated per-test in-memory SQLite engine. ``Base.metadata.create_all``
materialises the schema; ``app.database``'s ``@compiles(JSONB, "sqlite")``
shim makes Postgres-only column types portable. Each test gets a fresh
engine + session, disposed at teardown — no cross-test pollution and no
contact with the local dev ``wealth_agent.db``.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import AsyncIterator, Awaitable, Callable

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Side-effect imports: register every model with ``Base.metadata`` so
# ``create_all`` materialises the entire schema (FK targets like ``users`` and
# ``mf_fund_metadata`` must exist before children are created).
import app.models  # noqa: F401  -- registers all ORM tables with Base.metadata
from app.database import Base
from app.models.mf.enums import (
    MfOptionType,
    MfPlanType,
    MfTransactionSource,
    MfTransactionType,
)
from app.models.mf.mf_fund_metadata import MfFundMetadata
from app.models.mf.mf_nav_history import MfNavHistory
from app.models.mf.mf_transaction import MfTransaction
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
        email=f"rebal_test_{suffix}@example.com",
        country_code="+91",
        mobile="9999999999",
        phone=f"+91-9999{suffix}",
    )
    db_session.add(user)
    await db_session.flush()
    return user


@pytest_asyncio.fixture
async def fixture_buy_txn_factory(
    db_session: AsyncSession,
) -> Callable[..., Awaitable[MfTransaction]]:
    async def _make(
        *, user: User, scheme_code: str,
        units: Decimal, nav: Decimal, txn_date: date,
    ) -> MfTransaction:
        await _ensure_fund_metadata(db_session, scheme_code)
        txn = MfTransaction(
            user_id=user.id,
            scheme_code=scheme_code,
            folio_number="TEST_FOLIO",
            transaction_type=MfTransactionType.BUY,
            transaction_date=txn_date,
            units=units,
            nav=nav,
            amount=units * nav,
            source_system=MfTransactionSource.MANUAL,
        )
        db_session.add(txn)
        await db_session.flush()
        return txn
    return _make


@pytest_asyncio.fixture
async def fixture_sell_txn_factory(
    db_session: AsyncSession,
) -> Callable[..., Awaitable[MfTransaction]]:
    async def _make(
        *, user: User, scheme_code: str,
        units: Decimal, nav: Decimal, txn_date: date,
    ) -> MfTransaction:
        await _ensure_fund_metadata(db_session, scheme_code)
        txn = MfTransaction(
            user_id=user.id,
            scheme_code=scheme_code,
            folio_number="TEST_FOLIO",
            transaction_type=MfTransactionType.SELL,
            transaction_date=txn_date,
            units=units,
            nav=nav,
            amount=units * nav,
            source_system=MfTransactionSource.MANUAL,
        )
        db_session.add(txn)
        await db_session.flush()
        return txn
    return _make


@pytest_asyncio.fixture
async def fixture_nav_isin_factory(
    db_session: AsyncSession,
) -> Callable[..., Awaitable[MfNavHistory]]:
    async def _make(
        *, scheme_code: str, isin: str,
        nav: Decimal | None = None, on_date: date | None = None,
    ) -> MfNavHistory:
        await _ensure_fund_metadata(db_session, scheme_code)
        row = MfNavHistory(
            scheme_code=scheme_code,
            isin=isin,
            scheme_name=f"Scheme {scheme_code}",
            mf_type="EQUITY",
            nav=nav if nav is not None else Decimal("100"),
            nav_date=on_date or date.today(),
        )
        db_session.add(row)
        await db_session.flush()
        return row
    return _make


async def _ensure_fund_metadata(db_session: AsyncSession, scheme_code: str) -> None:
    """Idempotent: insert ``MfFundMetadata`` if not already present."""
    from sqlalchemy import select

    existing = (await db_session.execute(
        select(MfFundMetadata).where(MfFundMetadata.scheme_code == scheme_code)
    )).scalar_one_or_none()
    if existing is not None:
        return
    db_session.add(MfFundMetadata(
        scheme_code=scheme_code,
        scheme_name=f"Scheme {scheme_code}",
        amc_name="Test AMC",
        category="Equity",
        sub_category="Large Cap Fund",
        plan_type=MfPlanType.DIRECT,
        option_type=MfOptionType.GROWTH,
        is_active=True,
        asset_class="equity",
    ))
    await db_session.flush()
