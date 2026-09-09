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
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload
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
from app.domains.portfolio.models.portfolio import (
    Portfolio,
    PortfolioAllocation,
    PortfolioHistory,
    PortfolioHolding,
)

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


@pytest_asyncio.fixture
async def db_session_portfolio():
    """A portfolio whose holdings span two snapshots."""
    cas_scope.install_cas_scope_listeners()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(CasUpload.__table__.create)
        await conn.run_sync(Portfolio.__table__.create)
        await conn.run_sync(PortfolioHolding.__table__.create)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    old, new = uuid.uuid4(), uuid.uuid4()
    async with factory() as session:
        portfolio = Portfolio(user_id=USER, name="Primary", is_primary=True)
        session.add(portfolio)
        await session.flush()
        session.add_all(
            [
                PortfolioHolding(
                    portfolio_id=portfolio.id,
                    cas_upload_id=cid,
                    instrument_name=name,
                    instrument_type="mutual_fund",
                    current_value=1.0,
                )
                for cid, name in ((old, "old-fund"), (new, "new-fund"))
            ]
        )
        await session.commit()
        try:
            yield session, portfolio.id, old, new
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


# --------------------------------------------------------------------------- query shapes
#
# The read filter is added by a `do_orm_execute` hook, so its reach is decided by
# SQLAlchemy, not by us. These pin the shapes the app actually uses — the ten
# `ORDER BY created_at DESC LIMIT 1` sites, the `select(Model.id) ... limit(1)`
# existence probes, the aggregates, and the joins. Any of them slipping past the
# hook is a stale plan or a double-counted holding, with no error to notice.


@pytest_asyncio.fixture
async def two_snapshots(db_session):
    old, new = uuid.uuid4(), uuid.uuid4()
    db_session.add_all(
        [_txn(cas_upload_id=old, amount=100.0), _txn(cas_upload_id=new, amount=200.0)]
    )
    await db_session.commit()
    return old, new


@pytest.mark.asyncio
async def test_column_only_select_is_scoped(db_session, two_snapshots):
    """`select(MfTransaction.id)` — the shape of every has-holdings probe."""
    _, new = two_snapshots
    with cas_scope.scoped_to(new):
        rows = (
            await db_session.execute(
                select(MfTransaction.id).where(MfTransaction.user_id == USER)
            )
        ).all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_order_by_limit_one_is_scoped(db_session, two_snapshots):
    """The ten latest-row reads: without the scope this returns the old statement."""
    _, new = two_snapshots
    with cas_scope.scoped_to(new):
        amount = (
            (
                await db_session.execute(
                    select(MfTransaction.amount)
                    .where(MfTransaction.user_id == USER)
                    .order_by(MfTransaction.created_at.desc())
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )
    assert float(amount) == 200.0


@pytest.mark.asyncio
async def test_aggregate_is_scoped(db_session, two_snapshots):
    """A SUM across snapshots is the double-counting failure in its purest form."""
    _, new = two_snapshots
    with cas_scope.scoped_to(new):
        total = (
            await db_session.execute(
                select(func.sum(MfTransaction.amount)).where(
                    MfTransaction.user_id == USER
                )
            )
        ).scalar()
    assert float(total) == 200.0


@pytest.mark.asyncio
async def test_join_and_eager_load_are_scoped(db_session_portfolio):
    """Portfolio children are reached through the container, not through user_id."""
    session, portfolio_id, _old, new = db_session_portfolio
    with cas_scope.scoped_to(new):
        direct = (
            (
                await session.execute(
                    select(PortfolioHolding).where(
                        PortfolioHolding.portfolio_id == portfolio_id
                    )
                )
            )
            .scalars()
            .all()
        )
        joined = (
            (
                await session.execute(
                    select(Portfolio)
                    .join(
                        PortfolioHolding, PortfolioHolding.portfolio_id == Portfolio.id
                    )
                    .where(Portfolio.user_id == USER)
                )
            )
            .scalars()
            .all()
        )
        session.expire_all()
        eager = (
            (
                await session.execute(
                    select(Portfolio)
                    .options(selectinload(Portfolio.holdings))
                    .where(Portfolio.user_id == USER)
                )
            )
            .scalars()
            .first()
        )
    assert [h.instrument_name for h in direct] == ["new-fund"]
    assert len(joined) == 1
    assert [h.instrument_name for h in eager.holdings] == ["new-fund"]


# --------------------------------------------------------------------------- drift guards


def test_the_stamped_table_list_has_one_source():
    """The registry is the SSOT; the adoption lists must not drift from it.

    A table stamped in Python but missing from the adoption pass keeps its legacy
    rows NULL forever — permanently visible, and summed against every future
    statement.
    """
    from app.domains.ingestion.models.cas_upload import scoped_table_names

    assert set(scoped_table_names()) == set(cas_upload_service.SCOPED_TABLES)
    assert set(cas_upload_service._ADOPT_BY_USER_ID).isdisjoint(
        cas_upload_service._ADOPT_BY_PORTFOLIO
    )


def test_backfill_script_covers_the_same_tables():
    """The rollout script talks to asyncpg directly, so it carries its own copy."""
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[4] / "scripts" / "backfill_cas_uploads.py"
    spec = importlib.util.spec_from_file_location("_backfill_cas_uploads", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert set(module.BY_USER_ID) == set(cas_upload_service._ADOPT_BY_USER_ID)
    assert set(module.BY_PORTFOLIO) == set(cas_upload_service._ADOPT_BY_PORTFOLIO)


def test_portfolio_container_is_not_versioned():
    """Trap: versioning the container would break 8 `get_or_create_primary_portfolio`
    call sites and the goal-holding FKs that hang off its id. Children are
    versioned; the container is one per user."""
    from app.domains.ingestion.models.cas_upload import CasScoped

    assert not issubclass(Portfolio, CasScoped)
    for child in (PortfolioHolding, PortfolioAllocation, PortfolioHistory):
        assert issubclass(child, CasScoped), child


def test_latest_snapshot_uniqueness_includes_the_statement():
    """On (user_id, scheme_code) alone, a second statement holding the same fund
    could not be written at all."""
    from app.domains.mutual_funds.models.user_mf_latest_snapshot import (
        UserMfLatestSnapshot,
    )

    unique = {
        idx.name: [c.name for c in idx.columns]
        for idx in UserMfLatestSnapshot.__table__.indexes
        if idx.unique
    }
    assert "uq_user_mf_latest_snapshot_user_cas_scheme" in unique
    assert set(unique["uq_user_mf_latest_snapshot_user_cas_scheme"]) == {
        "user_id",
        "cas_upload_id",
        "scheme_code",
    }
    # The legacy partial index keeps one-row-per-fund for unstamped rows, which
    # the 3-column index cannot: NULL never equals NULL in a unique index.
    assert "uq_user_mf_latest_snapshot_user_scheme_legacy" in unique


def test_erasure_reaches_every_snapshot():
    """DPDP: an erasure that leaves superseded statements behind is not an erasure.

    The purge walks the live FK graph rather than a list, so what has to be true
    is structural — `cas_uploads` hangs off `users` with ON DELETE CASCADE, and
    every stamped table points back at it.
    """
    from app.domains.privacy.services.user_graph import _NEVER_WALK

    assert "cas_uploads" not in _NEVER_WALK

    user_fks = [
        fk for fk in CasUpload.__table__.foreign_keys if fk.column.table.name == "users"
    ]
    assert len(user_fks) == 1
    assert user_fks[0].ondelete == "CASCADE"

    from app.core.database import Base
    from app.domains.ingestion.models.cas_upload import scoped_table_names

    for name in scoped_table_names():
        col = Base.metadata.tables[name].c["cas_upload_id"]
        fks = list(col.foreign_keys)
        assert fks, f"{name}.cas_upload_id has no FK"
        assert fks[0].column.table.name == "cas_uploads", name
        # SET NULL, not CASCADE: pruning a snapshot header must never be able to
        # take a user's ledger with it.
        assert fks[0].ondelete == "SET NULL", name


def test_one_active_snapshot_per_user_is_a_database_rule():
    """The invariant is a partial unique index, created in the startup patches —
    application code is not what keeps it true."""
    import inspect

    from app.core import database

    source = inspect.getsource(database.apply_postgres_schema_patches)
    assert "uq_cas_uploads_one_active" in source
    assert "WHERE status = 'active'" in source


def test_every_cas_derived_table_is_classified():
    """The scan test: a new table holding CAS-derived state cannot slip through.

    `reset_user_financial_data` is the historical inventory of "everything an
    upload rebuilds". Each of its tables must be one of three things, and adding
    a new one to that list without deciding which is exactly the mistake this
    catches — an unstamped CAS-derived table keeps its rows visible under every
    snapshot and silently doubles a user's holdings on their next upload.
    """
    import re

    from app.domains.ingestion.models.cas_upload import scoped_table_names
    from app.domains.ingestion.services import user_data_reset

    reset_tables = {
        re.search(r"DELETE FROM (\w+)", stmt).group(1)
        for stmt in user_data_reset._RESET_STATEMENTS
    }

    # (1) Stamped: carries cas_upload_id, filtered on read.
    scoped = set(scoped_table_names())

    # (2) Children: reached through a stamped parent's FK (run_id, portfolio_id,
    #     bucket_id, aa_import_id), so they inherit its snapshot and need no
    #     column of their own.
    children = {
        "rebalancing_trades",
        "rebalancing_warnings",
        "rebalancing_subgroup_summaries",
        "rebalancing_fund_rows",
        "rebalancing_totals",
        "asset_allocation_bucket_run_targets",
        "asset_allocation_bucket_asset_classes",
        "asset_allocation_bucket_subgroups",
        "asset_allocation_buckets",
        "asset_allocation_run_targets",
        "asset_allocation_aggregate",
        "additional_investment_targets",
        "additional_investment_buys",
        "cashflow_plan_summary",
        "cashflow_fund_flow_summary",
        "cashflow_headline",
        "cashflow_monthly_rows",
        "cashflow_annual_rows",
        "mf_aa_transactions",
        "mf_aa_summaries",
    }

    # (3) User-owned: NOT derived from any statement, so never versioned — and,
    #     since versioning replaced the wipe, never deleted by an upload either.
    #     A CAS says nothing about a user's equity trades, cashflow assumptions,
    #     IPS or meeting notes; `portfolios` is the container, one per user.
    user_owned = {
        "cashflow_input_assumptions",
        "cashflow_input_one_off_events",
        "stock_transactions",
        "investment_policy_statements",
        "meeting_notes",
        "meeting_note_items",
        "portfolios",
    }

    # (4) Rebuilt-in-place: CAS-derived, but keyed on something that leaves no room
    #     for a second snapshot's copy, so it is recomputed rather than versioned.
    #     ``user_portfolio_nav_history`` is one row per (user_id, recorded_date) —
    #     two statements cannot both own a value for the same day. Stamping it was a
    #     real outage: a re-upload superseded the snapshot the rows belonged to, the
    #     read filter hid the entire series, and the rebuild then collided with those
    #     still-present rows on the unique key and killed the backfill job at 98%.
    rebuilt_in_place = {
        "user_portfolio_nav_history",
    }

    unclassified = reset_tables - scoped - children - user_owned - rebuilt_in_place
    assert not unclassified, (
        f"CAS-derived tables with no snapshot decision: {sorted(unclassified)}. "
        "Give it the CasScoped mixin, or add it to `children`/`user_owned`/"
        "`rebuilt_in_place` here."
    )


def test_adoption_routes_each_table_by_a_column_it_actually_has():
    """A table in the wrong adoption list throws at runtime, on a real upload.

    The two lists differ only in how a row reaches its user — `user_id` directly,
    or `portfolio_id` through the container. Getting that wrong is not caught by
    anything else until a legacy user uploads their next statement.
    """
    from app.core.database import Base

    for name in cas_upload_service._ADOPT_BY_USER_ID:
        assert "user_id" in Base.metadata.tables[name].c, name
    for name in cas_upload_service._ADOPT_BY_PORTFOLIO:
        cols = Base.metadata.tables[name].c
        assert "portfolio_id" in cols, name
        assert "user_id" not in cols, f"{name} has user_id — use the simpler list"


# ─────────────────── the net-worth series is NOT snapshot-scoped ───────────────────
# This is the outage these three tests exist to prevent recurring. `user_portfolio_
# nav_history` carried the CasScoped mixin while being keyed (user_id, recorded_date).
# Two statements cannot both own a value for the same day, so the moment a user
# uploaded a second CAS: their whole series was stamped with the now-superseded
# snapshot and the read filter hid it (3,529 rows stored, 0 visible — an empty chart),
# and the rebuild's snapshot-scoped DELETE matched nothing while its INSERT covered the
# full window, colliding with those same hidden rows on the unique key. That killed the
# backfill mid-write, and because the failure handler wrote to the already-aborted
# session it could not even mark the job failed — it hung at 98% until reaped.


def test_nav_history_is_not_cas_scoped():
    """The daily series must never carry the mixin again.

    ``CasScoped`` is what ``with_loader_criteria`` keys the read filter on, so
    re-adding it re-hides every row belonging to a superseded statement.
    """
    from app.domains.ingestion.models.cas_upload import CasScoped
    from app.domains.portfolio.models.user_portfolio_nav_history import (
        UserPortfolioNavHistory,
    )

    assert not issubclass(UserPortfolioNavHistory, CasScoped), (
        "user_portfolio_nav_history is keyed (user_id, recorded_date) and cannot be "
        "snapshot-scoped: a second statement has nowhere to put its copy of a day. "
        "Rebuild it in place instead."
    )
    assert "user_portfolio_nav_history" not in set(scoped_table_names_for_test()), (
        "the table is back in the scoped set — the read filter will hide superseded rows"
    )


def scoped_table_names_for_test():
    from app.domains.ingestion.models.cas_upload import scoped_table_names

    return scoped_table_names()


@pytest_asyncio.fixture
async def db_session_nav_history():
    """A user whose net-worth series was written under an earlier, now-superseded CAS."""
    from app.domains.portfolio.models.user_portfolio_nav_history import (
        UserPortfolioNavHistory,
    )

    cas_scope.install_cas_scope_listeners()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(CasUpload.__table__.create)
        await conn.run_sync(UserPortfolioNavHistory.__table__.create)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    old_snap, new_snap = uuid.uuid4(), uuid.uuid4()
    async with factory() as session:
        session.add_all(
            [
                CasUpload(
                    id=old_snap,
                    user_id=USER,
                    status=CasUploadStatus.SUPERSEDED.value,
                    content_sha256="a" * 64,
                ),
                CasUpload(
                    id=new_snap,
                    user_id=USER,
                    status=CasUploadStatus.ACTIVE.value,
                    content_sha256="b" * 64,
                ),
            ]
        )
        # Three days of series, stamped with the OLD statement — exactly the state a
        # re-upload leaves behind.
        session.add_all(
            [
                UserPortfolioNavHistory(
                    user_id=USER,
                    recorded_date=date(2024, 1, day),
                    total_value=100 * day,
                    total_invested=90 * day,
                    gain_percentage=11.1,
                    cas_upload_id=old_snap,
                )
                for day in (1, 2, 3)
            ]
        )
        await session.commit()
        try:
            yield session, new_snap
        finally:
            await session.rollback()
    await engine.dispose()


async def test_series_written_under_an_old_statement_stays_visible(
    db_session_nav_history,
):
    """The regression that emptied the chart: rows must survive their snapshot being superseded."""
    from app.domains.portfolio.models.user_portfolio_nav_history import (
        UserPortfolioNavHistory,
    )

    session, new_snap = db_session_nav_history
    with cas_scope.scoped_to(new_snap):
        rows = (
            (
                await session.execute(
                    select(UserPortfolioNavHistory).where(
                        UserPortfolioNavHistory.user_id == USER
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) == 3, (
        "the read filter hid a superseded statement's series — this is the empty chart"
    )


async def test_rebuilding_the_series_over_another_snapshot_does_not_collide(
    db_session_nav_history,
):
    """The regression that killed the job at 98%: a rebuild must be idempotent.

    Under the active snapshot, re-writing days that already exist under a superseded
    one used to raise UniqueViolationError on ``uq_user_nav_history_user_date``.
    """
    from app.domains.portfolio.models.user_portfolio_nav_history import (
        UserPortfolioNavHistory,
    )
    from app.domains.portfolio.services.networth_history_service import _persist_series

    session, new_snap = db_session_nav_history
    rows = [
        {
            "id": uuid.uuid4(),
            "user_id": USER,
            "recorded_date": date(2024, 1, day),
            "total_value": 500.0 + day,
            "total_invested": 400.0,
            "gain_percentage": 25.0,
            "cas_upload_id": new_snap,
        }
        for day in (1, 2, 3, 4)
    ]

    with cas_scope.scoped_to(new_snap):
        await _persist_series(session, USER, rows, snapshot_id=new_snap)
        # Twice: the second pass is the one that used to blow up.
        await _persist_series(session, USER, rows, snapshot_id=new_snap)

        stored = (
            (
                await session.execute(
                    select(UserPortfolioNavHistory)
                    .where(UserPortfolioNavHistory.user_id == USER)
                    .order_by(UserPortfolioNavHistory.recorded_date)
                )
            )
            .scalars()
            .all()
        )

    assert [r.recorded_date for r in stored] == [date(2024, 1, d) for d in (1, 2, 3, 4)]
    assert float(stored[0].total_value) == 501.0, (
        "the upsert must overwrite the superseded statement's value, not duplicate it"
    )


async def test_persist_series_prunes_days_outside_the_new_window(
    db_session_nav_history,
):
    """A shorter statement must not leave a cliff where the old series carried on.

    The series has to come from ONE ledger end to end: leaving the previous
    statement's later days in place would render as the portfolio falling off a
    cliff on the day the new statement starts.
    """
    from app.domains.portfolio.models.user_portfolio_nav_history import (
        UserPortfolioNavHistory,
    )
    from app.domains.portfolio.services.networth_history_service import _persist_series

    session, new_snap = db_session_nav_history
    rows = [
        {
            "id": uuid.uuid4(),
            "user_id": USER,
            "recorded_date": date(2024, 1, 3),
            "total_value": 700.0,
            "total_invested": 600.0,
            "gain_percentage": 16.6,
            "cas_upload_id": new_snap,
        }
    ]
    with cas_scope.scoped_to(new_snap):
        await _persist_series(session, USER, rows, snapshot_id=new_snap)
        stored = (
            (
                await session.execute(
                    select(UserPortfolioNavHistory).where(
                        UserPortfolioNavHistory.user_id == USER
                    )
                )
            )
            .scalars()
            .all()
        )
    assert [r.recorded_date for r in stored] == [date(2024, 1, 3)]
