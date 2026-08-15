"""Cost-basis regression tests for the latest-snapshot rebuild.

``invested_amount`` is the cost of the units still held, NOT net cash flow. The
rebuild used to subtract redemption *proceeds*, which drove a profitable
part-exit negative (production: HDFC Small Cap at invested = -658,135.88,
avg_nav = -157.9975) and, on a nearly-exited fund, overflowed avg_nav's
NUMERIC(12,4) — aborting the rebuild for 6 of 69 users on every run.
"""

from __future__ import annotations

import uuid
from datetime import date

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.all_models  # noqa: F401  -- registers the `users` FK target with Base.metadata
from app.domains.mutual_funds.models import (
    MfFundMetadata,
    MfNavHistory,
    MfTransaction,
    UserMfLatestSnapshot,
)
from app.domains.mutual_funds.models.enums import MfTransactionType
from app.domains.mutual_funds.services.latest_snapshot_service import (
    rebuild_user_latest_snapshot,
)

SCHEME = "113177"


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        # Base.metadata.create_all fails on sqlite (an unrelated model uses a
        # Postgres ARRAY) — create only the tables under test.
        for model in (MfTransaction, MfFundMetadata, MfNavHistory, UserMfLatestSnapshot):
            await conn.run_sync(model.__table__.create)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        yield session
    await engine.dispose()


def _txn(user_id, ttype, units, amount, when):
    return MfTransaction(
        user_id=user_id,
        scheme_code=SCHEME,
        folio_number="401161707486",
        transaction_type=ttype,
        # CAS writes redemption units as a negative number.
        units=-units if ttype is MfTransactionType.SELL else units,
        nav=amount / units,
        amount=amount,
        transaction_date=when,
    )


async def _rebuild(db, txns, *, nav: float) -> UserMfLatestSnapshot | None:
    user_id = uuid.uuid4()
    db.add_all([_txn(user_id, *t) for t in txns])
    db.add(
        MfNavHistory(
            scheme_code=SCHEME,
            scheme_name="Test Fund - Growth",
            mf_type="Open Ended Schemes",
            nav_date=date.today(),
            nav=nav,
        )
    )
    await db.commit()

    await rebuild_user_latest_snapshot(db, user_id)

    rows = (
        (await db.execute(UserMfLatestSnapshot.__table__.select())).mappings().all()
    )
    return rows[0] if rows else None


async def test_part_exit_keeps_cost_basis_of_units_still_held(db):
    """Buy 100 @ 100, sell 50 @ 200 — the remaining 50 still cost 100 each."""
    row = await _rebuild(
        db,
        [
            (MfTransactionType.BUY, 100.0, 10_000.0, date(2020, 1, 1)),
            (MfTransactionType.SELL, 50.0, 10_000.0, date(2024, 1, 1)),
        ],
        nav=200.0,
    )

    assert row is not None
    assert float(row["current_units"]) == pytest.approx(50.0)
    # Net cash flow would be 0 here; cost basis is 50 units x 100.
    assert float(row["invested_amount"]) == pytest.approx(5_000.0)
    assert float(row["avg_nav"]) == pytest.approx(100.0)
    assert float(row["current_value"]) == pytest.approx(10_000.0)
    # Unrealised only — the gain already booked on the sold half is excluded.
    assert float(row["unrealized_pnl"]) == pytest.approx(5_000.0)
    assert float(row["absolute_return_pct"]) == pytest.approx(100.0)


async def test_near_full_exit_does_not_overflow_avg_nav(db):
    """The production crash: a big appreciated position sold down to a crumb.

    Old maths: invested = 10,000 - 658,135 = -648,135 over 0.005 units =
    -1.3e8, past the NUMERIC(12,4) ceiling, so the INSERT aborted the user.
    """
    row = await _rebuild(
        db,
        [
            (MfTransactionType.BUY, 4_165.489, 10_000.0, date(2016, 12, 14)),
            (MfTransactionType.SELL, 4_165.484, 658_135.88, date(2024, 11, 26)),
        ],
        nav=141.792,
    )

    assert row is not None
    assert abs(float(row["avg_nav"])) < 1e8
    assert float(row["avg_nav"]) > 0
    assert float(row["invested_amount"]) > 0


async def test_absurd_return_drops_the_pct_not_the_holding(db):
    """A CAS row booking 11,453.263 units for Rs 10 (production, scheme 152778).

    absolute_return_pct comes out at 1,181,624.7698 — past the NUMERIC(10,4)
    ceiling of 10^6 — which used to abort the user's entire rebuild. The
    percentage is dropped; the holding itself still gets written.
    """
    row = await _rebuild(
        db,
        [(MfTransactionType.BUY, 11_453.263, 10.0, date(2026, 1, 19))],
        nav=10.3178,
    )

    assert row is not None
    assert row["absolute_return_pct"] is None
    # The position itself survives — units and value are real.
    assert float(row["current_units"]) == pytest.approx(11_453.263)
    assert float(row["current_value"]) == pytest.approx(118_172.48, abs=0.01)


async def test_full_exit_is_not_a_holding(db):
    row = await _rebuild(
        db,
        [
            (MfTransactionType.BUY, 100.0, 10_000.0, date(2020, 1, 1)),
            (MfTransactionType.SELL, 100.0, 20_000.0, date(2024, 1, 1)),
        ],
        nav=200.0,
    )
    assert row is None
