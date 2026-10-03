"""Scope regression tests for the latest-snapshot rebuild.

The rebuild resolves the user's active CAS snapshot and scopes its DELETE to it,
but the ``before_flush`` stamper and the ``do_orm_execute`` filter read the scope
from a ContextVar, not from an argument. So a caller that had not ENTERED the
scope — the nightly ``mfapi`` job, which calls
``rebuild_all_users_latest_snapshot`` with no scope set — deleted the snapshot's
rows and then inserted the replacements unstamped.

For a user who also carried unstamped rows the scoped DELETE had left in place,
that INSERT hit ``uq_user_mf_latest_snapshot_user_scheme_legacy`` and the whole
user was rolled back and skipped: 12 of 85 on the 2026-09-11 run, every one of
them a user with an active ``cas_uploads`` row and no other trait in common.
"""

from __future__ import annotations

import uuid
from datetime import date

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.all_models  # noqa: F401  -- registers the `users` FK target with Base.metadata
from app.core.cas_scope import get_scope, install_cas_scope_listeners
from app.domains.ingestion.models.cas_upload import CasUpload, CasUploadStatus
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
    # The hooks are what this file is about; installing them is idempotent.
    install_cas_scope_listeners()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        # Base.metadata.create_all fails on sqlite (an unrelated model uses a
        # Postgres ARRAY) — create only the tables under test.
        for model in (
            MfTransaction,
            MfFundMetadata,
            MfNavHistory,
            UserMfLatestSnapshot,
            CasUpload,
        ):
            await conn.run_sync(model.__table__.create)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        yield session
    await engine.dispose()


async def _seed(db, *, stale_row_stamped_with: uuid.UUID | None) -> tuple:
    """A user with one active snapshot, one buy, and one pre-existing cache row."""
    user_id = uuid.uuid4()
    upload = CasUpload(user_id=user_id, seq=1, status=CasUploadStatus.ACTIVE.value)
    db.add(upload)
    db.add(
        MfTransaction(
            user_id=user_id,
            scheme_code=SCHEME,
            folio_number="401161707486",
            transaction_type=MfTransactionType.BUY,
            units=100.0,
            nav=100.0,
            amount=10_000.0,
            transaction_date=date(2020, 1, 1),
            cas_upload_id=upload.id,
        )
    )
    db.add(
        MfNavHistory(
            scheme_code=SCHEME,
            scheme_name="Test Fund - Growth",
            mf_type="Open Ended Schemes",
            nav_date=date.today(),
            nav=200.0,
        )
    )
    # The row a previous unscoped run left behind. Explicitly assigned, so the
    # before_flush stamper leaves it alone.
    db.add(
        UserMfLatestSnapshot(
            user_id=user_id,
            scheme_code=SCHEME,
            current_units=1.0,
            invested_amount=1.0,
            current_value=1.0,
            unrealized_pnl=0.0,
            cas_upload_id=stale_row_stamped_with,
        )
    )
    await db.commit()
    return user_id, upload.id


async def _rows(db, user_id) -> list:
    result = await db.execute(
        UserMfLatestSnapshot.__table__.select().where(
            UserMfLatestSnapshot.__table__.c.user_id == user_id
        )
    )
    return result.mappings().all()


async def test_unscoped_caller_does_not_hit_the_legacy_unique_index(db):
    """The exact 2026-09-11 failure: no scope set + a leftover unstamped row.

    Before the fix this raised IntegrityError on
    uq_user_mf_latest_snapshot_user_scheme_legacy and the user was skipped.
    """
    user_id, upload_id = await _seed(db, stale_row_stamped_with=None)
    assert get_scope() is None, "the nightly job enters no scope — that is the bug"

    written = await rebuild_user_latest_snapshot(db, user_id)

    assert written == 1
    rows = await _rows(db, user_id)
    assert len(rows) == 1, "the leftover unstamped row must not survive as a duplicate"
    assert rows[0]["cas_upload_id"] == upload_id, "the new row carries the snapshot id"
    # Rebuilt from the transaction, not the 1.0 placeholder left by the old run.
    assert float(rows[0]["current_units"]) == pytest.approx(100.0)


async def test_rebuild_restores_the_previous_scope(db):
    """Entering the scope must not leak out of the call — the job loops users."""
    user_id, _ = await _seed(db, stale_row_stamped_with=None)

    await rebuild_user_latest_snapshot(db, user_id)

    assert get_scope() is None


async def test_rebuild_replaces_its_own_snapshot_rows(db):
    """The ordinary path: a stamped row for this snapshot is replaced, not doubled."""
    user_id = uuid.uuid4()
    upload = CasUpload(user_id=user_id, seq=1, status=CasUploadStatus.ACTIVE.value)
    db.add(upload)
    await db.flush()
    db.add(
        MfTransaction(
            user_id=user_id,
            scheme_code=SCHEME,
            folio_number="401161707486",
            transaction_type=MfTransactionType.BUY,
            units=100.0,
            nav=100.0,
            amount=10_000.0,
            transaction_date=date(2020, 1, 1),
            cas_upload_id=upload.id,
        )
    )
    db.add(
        MfNavHistory(
            scheme_code=SCHEME,
            scheme_name="Test Fund - Growth",
            mf_type="Open Ended Schemes",
            nav_date=date.today(),
            nav=200.0,
        )
    )
    db.add(
        UserMfLatestSnapshot(
            user_id=user_id,
            scheme_code=SCHEME,
            current_units=1.0,
            invested_amount=1.0,
            current_value=1.0,
            unrealized_pnl=0.0,
            cas_upload_id=upload.id,
        )
    )
    await db.commit()

    await rebuild_user_latest_snapshot(db, user_id)

    rows = await _rows(db, user_id)
    assert len(rows) == 1
    assert float(rows[0]["current_units"]) == pytest.approx(100.0)


async def test_user_without_a_snapshot_keeps_unstamped_rows_rebuilt(db):
    """No cas_uploads row → no scope → the pre-feature rebuild-everything path.

    This is the 73 users the nightly run has always handled fine; the widened
    DELETE must not change what happens to them.
    """
    user_id = uuid.uuid4()
    db.add(
        MfTransaction(
            user_id=user_id,
            scheme_code=SCHEME,
            folio_number="401161707486",
            transaction_type=MfTransactionType.BUY,
            units=100.0,
            nav=100.0,
            amount=10_000.0,
            transaction_date=date(2020, 1, 1),
        )
    )
    db.add(
        MfNavHistory(
            scheme_code=SCHEME,
            scheme_name="Test Fund - Growth",
            mf_type="Open Ended Schemes",
            nav_date=date.today(),
            nav=200.0,
        )
    )
    await db.commit()

    await rebuild_user_latest_snapshot(db, user_id)

    rows = await _rows(db, user_id)
    assert len(rows) == 1
    assert rows[0]["cas_upload_id"] is None
    assert float(rows[0]["current_units"]) == pytest.approx(100.0)


async def test_another_users_rows_are_never_touched(db):
    """The widened DELETE is still confined to one user."""
    victim = uuid.uuid4()
    db.add(
        UserMfLatestSnapshot(
            user_id=victim,
            scheme_code=SCHEME,
            current_units=7.0,
            invested_amount=7.0,
            current_value=7.0,
            unrealized_pnl=0.0,
            cas_upload_id=None,
        )
    )
    user_id, _ = await _seed(db, stale_row_stamped_with=None)

    await rebuild_user_latest_snapshot(db, user_id)

    rows = await _rows(db, victim)
    assert len(rows) == 1
    assert float(rows[0]["current_units"]) == pytest.approx(7.0)
