"""CAS snapshot scoping — the two session hooks, the lifecycle, and the fingerprint.

These cover the failure modes that would be silent in production: a read that
sums two statements together, a write that forgets which statement it came from,
a second upload whose every transaction collides with the first, and two active
snapshots at once.
"""

from __future__ import annotations

import uuid
from datetime import date

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

import app.all_models  # noqa: F401  -- registers FK target tables with Base.metadata
from app.core import cas_scope
from app.domains.ingestion.models.cas_upload import CasUpload, CasUploadStatus
from app.domains.ingestion.services import cas_upload_service
from app.domains.ingestion.services.mf_aa_normalizer import _build_fingerprint
from app.domains.mutual_funds.models.enums import (
    MfTransactionSource,
    MfTransactionType,
)
from app.domains.mutual_funds.models.mf_transaction import MfTransaction

# Deliberately not an all-digits UUID: sqlite gives the "UUID" column NUMERIC
# affinity, so a hex value like 1111…1111 is stored as a REAL and comes back as a
# float. Postgres (native uuid) has no such problem — this only bites the harness.
USER = uuid.UUID("a1b2c3d4-1111-4111-8111-aaaaaaaaaaaa")


@pytest_asyncio.fixture
async def db_session():
    cas_scope.install_cas_scope_listeners()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        # Base.metadata.create_all fails on sqlite (an unrelated model uses a
        # Postgres ARRAY), so only the tables under test are created.
        await conn.run_sync(CasUpload.__table__.create)
        await conn.run_sync(MfTransaction.__table__.create)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        try:
            yield session
        finally:
            await session.rollback()
    await engine.dispose()


def _txn(*, cas_upload_id: uuid.UUID | None, amount: float) -> MfTransaction:
    return MfTransaction(
        user_id=USER,
        cas_upload_id=cas_upload_id,
        scheme_code="120716",
        folio_number="F1",
        transaction_type=MfTransactionType.BUY,
        transaction_date=date(2026, 1, 1),
        units=10.0,
        nav=100.0,
        amount=amount,
        source_system=MfTransactionSource.AA,
    )


async def _amounts(db: AsyncSession) -> list[float]:
    rows = (
        (await db.execute(select(MfTransaction).where(MfTransaction.user_id == USER)))
        .scalars()
        .all()
    )
    return sorted(float(r.amount) for r in rows)


@pytest.mark.asyncio
async def test_reads_see_only_the_active_snapshot_plus_unowned_rows(db_session):
    """The core promise: a second statement replaces the first on screen.

    The NULL row stands for a manual entry or a SimBanks sync — never owned by a
    statement, so never hidden by one.
    """
    old, new = uuid.uuid4(), uuid.uuid4()
    db_session.add_all(
        [
            _txn(cas_upload_id=old, amount=1000.0),
            _txn(cas_upload_id=new, amount=2000.0),
            _txn(cas_upload_id=None, amount=50.0),
        ]
    )
    await db_session.commit()

    with cas_scope.scoped_to(new):
        assert await _amounts(db_session) == [50.0, 2000.0]

    # Same process, different scope: proves the filter is not cached against the
    # first snapshot id it ever saw — which would serve one user another's data.
    with cas_scope.scoped_to(old):
        assert await _amounts(db_session) == [50.0, 1000.0]

    with cas_scope.unscoped():
        assert await _amounts(db_session) == [50.0, 1000.0, 2000.0]


@pytest.mark.asyncio
async def test_new_rows_are_stamped_with_the_active_snapshot(db_session):
    """Any plan, projection or ledger row written in scope records its statement."""
    snapshot = uuid.uuid4()
    with cas_scope.scoped_to(snapshot):
        db_session.add(_txn(cas_upload_id=None, amount=777.0))
        await db_session.commit()

    with cas_scope.unscoped():
        row = (
            await db_session.execute(
                select(MfTransaction).where(MfTransaction.amount == 777.0)
            )
        ).scalar_one()
    assert row.cas_upload_id == snapshot


@pytest.mark.asyncio
async def test_an_explicit_snapshot_id_is_never_overwritten(db_session):
    """Adoption and the ingest set ids by hand; the stamper must not fight them."""
    mine, other = uuid.uuid4(), uuid.uuid4()
    with cas_scope.scoped_to(other):
        db_session.add(_txn(cas_upload_id=mine, amount=42.0))
        await db_session.commit()

    with cas_scope.unscoped():
        row = (
            await db_session.execute(
                select(MfTransaction).where(MfTransaction.amount == 42.0)
            )
        ).scalar_one()
    assert row.cas_upload_id == mine


@pytest.mark.asyncio
async def test_activate_supersedes_the_previous_statement(db_session):
    first = await cas_upload_service.mint(db_session, USER, source_filename="one.pdf")
    await cas_upload_service.activate(db_session, first, total_value_inr=100.0)
    second = await cas_upload_service.mint(db_session, USER, source_filename="two.pdf")
    await cas_upload_service.activate(db_session, second, total_value_inr=250.0)
    await db_session.commit()

    rows = {
        r.seq: r
        for r in (
            await db_session.execute(select(CasUpload).where(CasUpload.user_id == USER))
        ).scalars()
    }
    assert rows[1].status == CasUploadStatus.SUPERSEDED.value
    assert rows[1].superseded_by_id == second.id
    assert rows[1].superseded_at is not None
    # The old statement keeps its own figures — that is the whole point.
    assert float(rows[1].total_value_inr) == 100.0
    assert rows[2].status == CasUploadStatus.ACTIVE.value
    assert rows[2].superseded_by_id is None
    assert await cas_scope.resolve_active_cas_upload_id(db_session, USER) == second.id


@pytest.mark.asyncio
async def test_mint_numbers_uploads_per_user(db_session):
    a = await cas_upload_service.mint(db_session, USER, source_filename="a.pdf")
    b = await cas_upload_service.mint(db_session, USER, source_filename="b.pdf")
    other_user = await cas_upload_service.mint(db_session, uuid.uuid4())
    assert (a.seq, b.seq, other_user.seq) == (1, 2, 1)


@pytest.mark.asyncio
async def test_identical_reupload_is_recognised(db_session):
    sha = cas_upload_service.sha256_of(b"%PDF-1.7 statement bytes")
    snapshot = await cas_upload_service.mint(db_session, USER, content_sha256=sha)
    await cas_upload_service.activate(db_session, snapshot)
    await db_session.commit()

    assert (
        await cas_upload_service.find_identical_active(db_session, USER, sha)
    ).id == snapshot.id
    assert (
        await cas_upload_service.find_identical_active(db_session, USER, "deadbeef")
    ) is None


def test_fingerprint_is_scoped_to_the_snapshot():
    """Without this, upload #2 collides on every row and lands an empty ledger."""
    common = dict(
        user_id=USER,
        scheme_code="120716",
        folio_number="F1",
        transaction_type=MfTransactionType.BUY,
        transaction_date=date(2026, 1, 1),
        units=10.0,
        nav=100.0,
        amount=1000.0,
    )
    first = _build_fingerprint(cas_upload_id=uuid.uuid4(), **common)
    second = _build_fingerprint(cas_upload_id=uuid.uuid4(), **common)
    assert first != second
    # Within one statement the key still dedupes a repeated line.
    same = uuid.uuid4()
    assert _build_fingerprint(cas_upload_id=same, **common) == _build_fingerprint(
        cas_upload_id=same, **common
    )


@pytest.mark.asyncio
async def test_scope_filter_only_applies_when_a_snapshot_is_known():
    """DELETEs fall back to the pre-snapshot 'replace everything' behaviour."""
    assert cas_scope.scope_filter(MfTransaction, None) == []
    assert len(cas_scope.scope_filter(MfTransaction, uuid.uuid4())) == 1
    assert len(cas_scope.non_snapshot_filter(MfTransaction)) == 1


@pytest.mark.asyncio
async def test_adoption_claims_only_unstamped_rows(db_session, monkeypatch):
    """Legacy data joins one snapshot; rows that already belong to one do not move."""
    # The harness creates only the tables under test, so the adoption pass is
    # pointed at the one it can see. The SQL is identical for the other 17.
    monkeypatch.setattr(cas_upload_service, "_ADOPT_BY_USER_ID", ("mf_transactions",))
    monkeypatch.setattr(cas_upload_service, "_ADOPT_BY_PORTFOLIO", ())
    legacy, existing = uuid.uuid4(), uuid.uuid4()
    db_session.add_all(
        [
            _txn(cas_upload_id=None, amount=10.0),
            _txn(cas_upload_id=None, amount=20.0),
            _txn(cas_upload_id=existing, amount=30.0),
        ]
    )
    await db_session.commit()

    counts = await cas_upload_service.adopt_unscoped_rows(db_session, USER, legacy)
    await db_session.commit()
    assert counts.get("mf_transactions") == 2

    with cas_scope.scoped_to(legacy):
        assert await _amounts(db_session) == [10.0, 20.0]
    with cas_scope.scoped_to(existing):
        assert await _amounts(db_session) == [30.0]


@pytest.mark.asyncio
async def test_versioning_can_be_switched_off(db_session, monkeypatch):
    """Kill switch: the hooks go inert and every row is visible again."""
    old, new = uuid.uuid4(), uuid.uuid4()
    db_session.add_all(
        [_txn(cas_upload_id=old, amount=1.0), _txn(cas_upload_id=new, amount=2.0)]
    )
    await db_session.commit()

    monkeypatch.setattr(cas_scope, "versioning_enabled", lambda: False)
    with cas_scope.scoped_to(new):
        assert await _amounts(db_session) == [1.0, 2.0]


@pytest.mark.asyncio
async def test_column_present_on_every_stamped_table():
    """The 18 tables the app reads as 'current' all carry the statement id."""
    from app.core.database import Base

    for table in cas_upload_service.SCOPED_TABLES:
        assert "cas_upload_id" in Base.metadata.tables[table].c, table
