"""Tests for compute_portfolio_xirr — the canonical portfolio money-weighted return.

Ingest stores redemptions (SELL/SWITCH_OUT) with NEGATIVE amount AND NEGATIVE units
(confirmed against real CAS data). The XIRR must treat a sell as a positive inflow
and reduce remaining units; direction comes from the transaction TYPE, magnitude from
abs(). These tests pin that convention down.
"""

from __future__ import annotations

import uuid
from datetime import date

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.all_models  # noqa: F401  -- registers every ORM Table so FK targets resolve
from financial_primitives.xirr import xirr
from app.domains.mutual_funds.models.enums import MfTransactionType
from app.domains.mutual_funds.models.mf_nav_history import MfNavHistory
from app.domains.mutual_funds.models.mf_transaction import MfTransaction
from app.domains.mutual_funds.services.xirr_service import compute_portfolio_xirr


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(MfTransaction.__table__.create)
        await conn.run_sync(MfNavHistory.__table__.create)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        try:
            yield session
        finally:
            await session.rollback()
    await engine.dispose()


def _txn(uid, ttype, d, amount, units, scheme="SCH1"):
    return MfTransaction(
        id=uuid.uuid4(), user_id=uid, scheme_code=scheme, folio_number="F1",
        transaction_type=ttype, transaction_date=d, units=units, nav=0.0, amount=amount,
    )


def _nav(d, nav, scheme="SCH1"):
    return MfNavHistory(
        id=uuid.uuid4(), scheme_code=scheme, scheme_name="Test Fund",
        mf_type="EQUITY", nav=nav, nav_date=d,
    )


@pytest.mark.asyncio
async def test_portfolio_xirr_handles_negative_sell_signs(db_session: AsyncSession):
    """A SELL stored as negative amount/units is an INFLOW that reduces units."""
    uid = uuid.uuid4()
    # Buy 100 units for ₹1000 (NAV 10); later sell 50 units for ₹600 — stored NEGATIVE.
    db_session.add(_txn(uid, MfTransactionType.BUY, date(2024, 1, 1), 1000.0, 100.0))
    db_session.add(_txn(uid, MfTransactionType.SELL, date(2024, 7, 1), -600.0, -50.0))
    # Latest NAV 14 → 50 units left are worth ₹700 as of 2025-01-01.
    db_session.add(_nav(date(2025, 1, 1), 14.0))
    await db_session.flush()

    res = await compute_portfolio_xirr(db_session, uid)

    # 50 units (100 bought − 50 sold) × ₹14, NOT 150 units × ₹14 = ₹2100.
    assert res.current_value == pytest.approx(700.0)
    assert res.invested == pytest.approx(1000.0)
    # XIRR must match the hand-built correct cashflows (sell is a +600 inflow).
    expected = xirr([
        (date(2024, 1, 1), -1000.0),
        (date(2024, 7, 1), 600.0),
        (date(2025, 1, 1), 700.0),
    ])
    assert res.xirr == pytest.approx(expected, abs=1e-6)
