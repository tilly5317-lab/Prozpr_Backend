"""Twenty-three scenarios for ONE invariant: today's net worth sums the ACTIVE CAS
statement's funds only — never a superseded upload's — and the rebuild, the daily
refresh, the as-of valuer and the dashboard headline all land on that same number.

Every scenario drives the real code against a real Postgres: the daily job is one
set-based statement (LATERAL, ON CONFLICT), the writer uses unnest arrays, and the
CAS read hook is a session event — none of which sqlite can exercise. Ledgers are
hand-built with flat NAVs, so every expected rupee figure is known by construction.

Opt-in, because it needs a database, and it TRUNCATEs the tables it seeds between
scenarios — so it refuses to run against anything but a throwaway database::

    NETWORTH_PG_TEST=1 \\
    DATABASE_URL=postgresql+asyncpg://postgres@127.0.0.1:54329/networth_test \\
        .venv/Scripts/python -m pytest \\
        app/domains/portfolio/services/tests/test_networth_active_snapshot_scenarios.py -q

(this directory is gitignored, so a new file here needs ``git add -f``).
"""

from __future__ import annotations

import asyncio
import os
import random
import uuid
from datetime import date, timedelta
from decimal import Decimal as D

import pytest
import pytest_asyncio
from sqlalchemy import insert, text

_URL = os.environ.get("DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    os.environ.get("NETWORTH_PG_TEST") != "1" or "networth_test" not in _URL,
    reason=(
        "needs a THROWAWAY Postgres: set NETWORTH_PG_TEST=1 and point DATABASE_URL "
        "at a database named networth_test (this suite TRUNCATEs tables)"
    ),
)

# ── fixed world ──────────────────────────────────────────────────────────────
# Four fake funds with flat NAVs. Codes are outside the AMFI range so they can never
# collide with real data even if someone points this at the wrong database.
A, B, C, Z = "900001", "900002", "900003", "900009"
NAV = {A: D("100"), B: D("50"), C: D("20"), Z: D("1000")}

_SCHEMA_READY = False


def _today() -> date:
    from app.domains.portfolio.services.networth.clock import ist_today

    return ist_today()


# ── fixtures ─────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def pg(monkeypatch):
    """Engine + listeners + schema; wipes the seeded tables; no network, ever."""
    global _SCHEMA_READY
    import app.all_models  # noqa: F401 — every mapper, or the first relationship fails
    from app.core import cas_scope
    from app.core.database import (
        _get_session_factory,
        apply_postgres_schema_patches,
        create_all_tables,
        dispose_engine,
    )
    from app.domains.mutual_funds.services import nav_history_service
    from app.domains.portfolio.services.networth import nav_coverage

    # Belt and braces: NAV is seeded through today, so nothing should ever try to
    # fetch — but a scenario that accidentally did must fail loudly, not phone home.
    async def _no_network(*args, **kwargs):  # noqa: ARG001
        raise AssertionError("scenario tried to fetch NAV from the network")

    monkeypatch.setattr(
        nav_history_service, "_fetch_nav_from_source_for_scheme", _no_network
    )
    monkeypatch.setattr(nav_coverage, "ensure_nav_history_for_chart", _no_network)

    cas_scope.install_cas_scope_listeners()
    if not _SCHEMA_READY:
        await create_all_tables()
        try:
            await apply_postgres_schema_patches()
        except Exception:  # noqa: BLE001
            # The startup patch block targets a database that already has every
            # table (it ALTERs some that create_all does not make). On a fresh
            # throwaway DB only the two partial unique indexes matter here.
            await dispose_engine()
            factory = _get_session_factory()
            async with factory() as db:
                await db.execute(
                    text(
                        "CREATE UNIQUE INDEX IF NOT EXISTS uq_cas_uploads_one_active "
                        "ON cas_uploads (user_id) WHERE status = 'active'"
                    )
                )
                await db.execute(
                    text(
                        "CREATE UNIQUE INDEX IF NOT EXISTS uq_networth_job_active "
                        "ON portfolio_networth_jobs (user_id) "
                        "WHERE status IN ('pending', 'running')"
                    )
                )
                await db.commit()
        _SCHEMA_READY = True

    factory = _get_session_factory()
    async with factory() as db:
        await db.execute(
            text("TRUNCATE users, mf_fund_metadata, mf_nav_history CASCADE")
        )
        await db.commit()
    cas_scope.set_scope(None)
    try:
        yield factory
    finally:
        cas_scope.set_scope(None)
        await dispose_engine()


@pytest_asyncio.fixture
async def world(pg):
    """Fund metadata + weekly NAV from 400 days ago through today, for all four funds."""
    from app.domains.mutual_funds.models import MfFundMetadata, MfNavHistory
    from app.domains.mutual_funds.models.enums import MfOptionType, MfPlanType

    today = _today()
    async with pg() as db:
        for code in NAV:
            db.add(
                MfFundMetadata(
                    scheme_code=code,
                    scheme_name=f"Scenario Fund {code}",
                    amc_name="Scenario AMC",
                    category="Equity",
                    plan_type=MfPlanType.DIRECT,
                    option_type=MfOptionType.GROWTH,
                    is_active=True,
                )
            )
        await db.flush()
        rows = []
        for code, nav in NAV.items():
            day = today - timedelta(days=400)
            while day < today:
                rows.append(_nav_row(code, day, nav))
                day += timedelta(days=7)
            rows.append(_nav_row(code, today, nav))
        await db.execute(insert(MfNavHistory), rows)
        await db.commit()
    return pg


def _nav_row(code: str, day: date, nav: D) -> dict:
    return {
        "id": uuid.uuid4(),
        "scheme_code": code,
        "scheme_name": f"Scenario Fund {code}",
        "mf_type": "Open Ended",
        "nav": nav,
        "nav_date": day,
    }


# ── seeding helpers ──────────────────────────────────────────────────────────


async def new_user(pg) -> uuid.UUID:
    from app.domains.identity.models.user import User

    mobile = "9" + "".join(random.choice("0123456789") for _ in range(9))
    async with pg() as db:
        row = User(
            country_code="+91",
            mobile=mobile,
            phone=f"+91{mobile}",
            currency="INR",
            is_active=True,
            is_onboarding_complete=True,
            first_name="Scenario",
        )
        db.add(row)
        await db.commit()
        return row.id


async def _primary_portfolio(db, uid: uuid.UUID):
    from sqlalchemy import select

    from app.domains.portfolio.models.portfolio import Portfolio

    row = (
        await db.execute(
            select(Portfolio).where(Portfolio.user_id == uid, Portfolio.is_primary)
        )
    ).scalar_one_or_none()
    if row is None:
        row = Portfolio(
            user_id=uid,
            name="Primary",
            total_value=0,
            total_invested=0,
            is_primary=True,
        )
        db.add(row)
        await db.flush()
    return row


def _txn(code: str, days_ago: int, kind: str, units: str, nav: D | None = None):
    """(code, days_ago, BUY|SELL, units, nav). SELL units are NEGATIVE, as a CAS prints them."""
    return (code, days_ago, kind, D(units), nav if nav is not None else NAV[code])


async def upload(
    pg,
    uid: uuid.UUID,
    txns: list[tuple],
    holdings: list[tuple[str, str, str]] | None = None,
    *,
    activate: bool = True,
    owner: uuid.UUID | None | str = "self",
) -> uuid.UUID | None:
    """Seed one statement: a cas_uploads row, its ledger rows, its holdings rows.

    ``holdings`` = [(code, units, avg_cost)]; defaults to the ledger's own end
    positions so the anchor is ~0 unless a scenario wants otherwise.
    ``owner='self'`` stamps rows with the new upload; ``None`` leaves them unowned
    (a manual entry / legacy row); a UUID stamps them with that other upload.
    """
    from app.domains.ingestion.services import cas_upload_service
    from app.domains.mutual_funds.models import MfTransaction
    from app.domains.mutual_funds.models.enums import (
        MfTransactionSource,
        MfTransactionType,
    )
    from app.domains.portfolio.models.portfolio import PortfolioHolding

    today = _today()
    async with pg() as db:
        snap = None
        if owner == "self":
            snap = await cas_upload_service.mint(
                db, uid, content_sha256=uuid.uuid4().hex, source_filename="s.pdf"
            )
            await db.flush()
            stamp = snap.id
        else:
            stamp = owner  # None or another upload's id

        end_units: dict[str, D] = {}
        end_cost: dict[str, D] = {}
        for code, ago, kind, units, nav in txns:
            db.add(
                MfTransaction(
                    user_id=uid,
                    scheme_code=code,
                    folio_number="SCN/1",
                    transaction_type=MfTransactionType(kind),
                    transaction_date=today - timedelta(days=ago),
                    units=units,
                    nav=nav,
                    amount=abs(units) * nav,
                    source_system=MfTransactionSource.AA,
                    source_txn_fingerprint=uuid.uuid4().hex,
                    cas_upload_id=stamp,
                )
            )
            if kind in ("BUY", "SWITCH_IN"):
                end_units[code] = end_units.get(code, D(0)) + abs(units)
                end_cost[code] = end_cost.get(code, D(0)) + abs(units) * nav
            else:
                end_units[code] = end_units.get(code, D(0)) - abs(units)

        if holdings is None:
            holdings = [
                (code, str(u), str(end_cost.get(code, D(0)) / u))
                for code, u in end_units.items()
                if u > 0
            ]
        portfolio = await _primary_portfolio(db, uid)
        for code, units, cost in holdings:
            db.add(
                PortfolioHolding(
                    portfolio_id=portfolio.id,
                    instrument_name=f"Scenario Fund {code}",
                    instrument_type="mutual_fund",
                    ticker_symbol=code,
                    quantity=D(units),
                    average_cost=D(cost),
                    current_price=NAV[code],
                    current_value=D(units) * NAV[code],
                    cas_upload_id=stamp,
                )
            )
        if snap is not None and activate:
            await cas_upload_service.activate(db, snap)
        await db.commit()
        return snap.id if snap is not None else None


async def rebuild(pg, uid: uuid.UUID, *, pin_scope: bool = True):
    """The real build, as the background job runs it (scope resolved AND entered)."""
    from app.core.cas_scope import effective_scope, scoped_to
    from app.domains.portfolio.services.networth.builder import build_series

    async with pg() as db:
        if not pin_scope:
            # Nothing resolved, nothing entered — the shape of every scoping bug so
            # far. build_series must pin the statement on its own.
            return await build_series(db, uid)
        snapshot_id = await effective_scope(db, uid)
        with scoped_to(snapshot_id):
            return await build_series(db, uid, snapshot_id)


async def daily(pg, as_of: date | None = None) -> int:
    from app.domains.portfolio.services.networth.daily import refresh_day

    async with pg() as db:
        return await refresh_day(db, as_of or _today())


async def row_for(pg, uid: uuid.UUID, day: date | None = None):
    async with pg() as db:
        return (
            await db.execute(
                text(
                    "SELECT total_value, total_invested, gain_percentage, updated_at "
                    "FROM user_portfolio_nav_history "
                    "WHERE user_id = :u AND recorded_date = :d"
                ),
                {"u": uid, "d": day or _today()},
            )
        ).first()


async def positions(pg, uid: uuid.UUID) -> dict[str, D]:
    async with pg() as db:
        rows = (
            await db.execute(
                text(
                    "SELECT scheme_code, units FROM user_scheme_position WHERE user_id = :u"
                ),
                {"u": uid},
            )
        ).all()
    return {r.scheme_code: D(r.units) for r in rows}


async def state(pg, uid: uuid.UUID):
    async with pg() as db:
        return (
            await db.execute(
                text("SELECT * FROM user_networth_series_state WHERE user_id = :u"),
                {"u": uid},
            )
        ).first()


async def headline(pg, uid: uuid.UUID) -> D:
    async with pg() as db:
        return D(
            (
                await db.execute(
                    text(
                        "SELECT total_value FROM portfolios WHERE user_id = :u AND is_primary"
                    ),
                    {"u": uid},
                )
            ).scalar()
        )


async def unscoped_holdings_sum(pg, uid: uuid.UUID) -> D:
    """What a writer without the scope sums: every statement's holdings at once."""
    async with pg() as db:
        return D(
            (
                await db.execute(
                    text(
                        "SELECT COALESCE(SUM(ph.current_value), 0) FROM portfolio_holdings ph "
                        "JOIN portfolios p ON p.id = ph.portfolio_id WHERE p.user_id = :u"
                    ),
                    {"u": uid},
                )
            ).scalar()
        )


def value(**units_by_code: str) -> D:
    return sum((D(u) * NAV[c] for c, u in units_by_code.items()), D(0))


# ── scenarios ────────────────────────────────────────────────────────────────


async def test_01_single_statement_prices_its_own_funds(world):
    uid = await new_user(world)
    snap = await upload(
        world, uid, [_txn(A, 300, "BUY", "10"), _txn(B, 200, "BUY", "20")]
    )
    await rebuild(world, uid)
    await daily(world)

    row = await row_for(world, uid)
    assert D(row.total_value) == value(**{A: "10", B: "20"}) == D("2000")
    assert D(row.total_invested) == D("2000")
    assert (await state(world, uid)).built_from_cas_upload_id == snap


async def test_02_second_statement_drops_a_fund(world):
    uid = await new_user(world)
    await upload(world, uid, [_txn(A, 300, "BUY", "10"), _txn(B, 200, "BUY", "20")])
    await upload(world, uid, [_txn(A, 300, "BUY", "10")])  # B is gone from the new CAS
    await rebuild(world, uid)
    await daily(world)

    assert D((await row_for(world, uid)).total_value) == value(**{A: "10"})
    assert set(await positions(world, uid)) == {A}


async def test_03_second_statement_adds_a_fund_without_double_counting(world):
    uid = await new_user(world)
    await upload(world, uid, [_txn(A, 300, "BUY", "10")])
    await upload(world, uid, [_txn(A, 300, "BUY", "10"), _txn(C, 30, "BUY", "5")])
    await rebuild(world, uid)
    await daily(world)

    # A appears in both statements; it is held once, not twice.
    assert D((await row_for(world, uid)).total_value) == value(**{A: "10", C: "5"})


async def test_04_nine_identical_reuploads_count_once(world):
    """The 2026-09-13 shape: nine uploads of the same 6-fund CAS charted Rs 15.1 crore
    for a Rs 60,792 portfolio — every statement summed at once."""
    uid = await new_user(world)
    ledger = [_txn(A, 300, "BUY", "10"), _txn(B, 200, "BUY", "20")]
    for _ in range(9):
        await upload(world, uid, ledger)
    await rebuild(world, uid)
    await daily(world)

    assert (await unscoped_holdings_sum(world, uid)) == D("2000") * 9  # the trap
    assert D((await row_for(world, uid)).total_value) == D("2000")
    assert len(await positions(world, uid)) == 2


async def test_05_unowned_rows_stay_visible_superseded_rows_do_not(world):
    uid = await new_user(world)
    await upload(world, uid, [_txn(Z, 300, "BUY", "1")])  # superseded: Rs 1,000 of Z
    await upload(world, uid, [_txn(A, 300, "BUY", "10")])  # active
    await upload(world, uid, [_txn(C, 30, "BUY", "5")], owner=None)  # manual: unowned
    await rebuild(world, uid)
    await daily(world)

    assert D((await row_for(world, uid)).total_value) == value(**{A: "10", C: "5"})


async def test_06_rebuild_that_never_entered_the_scope_still_pins_the_statement(world):
    """The exact failure of the 2026-09-12 repair script: the scope was neither
    resolved nor entered, so every statement's ledger was summed."""
    from app.core.cas_scope import get_scope

    uid = await new_user(world)
    await upload(world, uid, [_txn(Z, 300, "BUY", "1")])
    await upload(world, uid, [_txn(A, 300, "BUY", "10")])
    assert get_scope() is None
    await rebuild(world, uid, pin_scope=False)
    await daily(world)

    assert D((await row_for(world, uid)).total_value) == value(**{A: "10"})
    assert Z not in await positions(world, uid)


async def test_07_daily_refresh_matches_the_rebuild_and_is_idempotent(world):
    uid = await new_user(world)
    await upload(world, uid, [_txn(A, 300, "BUY", "10"), _txn(B, 200, "BUY", "20")])
    await rebuild(world, uid)
    built = await row_for(world, uid)

    first = await daily(world)
    second = await daily(world)
    after = await row_for(world, uid)

    assert first == second == 1
    assert (D(after.total_value), D(after.total_invested)) == (
        D(built.total_value),
        D(built.total_invested),
    )


async def test_08_daily_refresh_reprices_at_the_newest_nav(world):
    uid = await new_user(world)
    await upload(world, uid, [_txn(A, 300, "BUY", "10")])
    await rebuild(world, uid)
    await daily(world)
    yesterday = await row_for(world, uid, _today() - timedelta(days=1))

    async with world() as db:
        await db.execute(
            text(
                "UPDATE mf_nav_history SET nav = 120 WHERE scheme_code = :c AND nav_date = :d"
            ),
            {"c": A, "d": _today()},
        )
        await db.commit()
    await daily(world)

    assert D((await row_for(world, uid)).total_value) == D("1200")
    assert D(
        (await row_for(world, uid, _today() - timedelta(days=1))).total_value
    ) == D(yesterday.total_value)


async def test_09_daily_refresh_heals_a_row_another_writer_corrupted(world):
    """What actually happened on dev: a stale process wrote today's point from the
    unscoped roll-up AFTER the daily pass. The next pass must put it right again."""
    uid = await new_user(world)
    await upload(world, uid, [_txn(Z, 300, "BUY", "1")])
    await upload(world, uid, [_txn(A, 300, "BUY", "10")])
    await rebuild(world, uid)
    await daily(world)

    polluted = await unscoped_holdings_sum(world, uid)
    async with world() as db:
        await db.execute(
            text(
                "UPDATE user_portfolio_nav_history SET total_value = :v, total_invested = :v "
                "WHERE user_id = :u AND recorded_date = :d"
            ),
            {"v": polluted, "u": uid, "d": _today()},
        )
        await db.commit()
    assert D((await row_for(world, uid)).total_value) == D("2000")

    await daily(world)
    row = await row_for(world, uid)
    assert (D(row.total_value), D(row.total_invested)) == (D("1000"), D("1000"))


async def test_10_daily_refresh_carries_the_anchor_like_the_rebuild(world):
    """Holdings say 15 units, the ledger only shows 10 bought: the rebuild anchors the
    series to the holdings total. The daily point must carry that same anchor, or the
    chart steps down by it every run (it did, by Rs 12.5 lakh on one account)."""
    uid = await new_user(world)
    await upload(world, uid, [_txn(A, 300, "BUY", "10")], holdings=[(A, "15", "100")])
    await rebuild(world, uid)
    built = await row_for(world, uid)
    st = await state(world, uid)
    assert D(st.anchor_value) == D("500")
    assert D(built.total_value) == D("1500")

    await daily(world)
    row = await row_for(world, uid)
    assert D(row.total_value) == D("1500")
    assert D(row.total_invested) == D(st.last_invested)
    assert D(row.gain_percentage) == D(built.gain_percentage)


async def test_11_revalue_without_scope_uses_active_holdings_only(world):
    from app.core.cas_scope import get_scope
    from app.domains.portfolio.services.portfolio_service import (
        revalue_primary_portfolio_at_latest_nav,
    )

    uid = await new_user(world)
    await upload(world, uid, [_txn(Z, 300, "BUY", "1")])
    await upload(world, uid, [_txn(A, 300, "BUY", "10")])
    assert get_scope() is None
    async with world() as db:
        portfolio = await revalue_primary_portfolio_at_latest_nav(db, uid)

    assert D(str(portfolio.total_value)) == D("1000")
    assert await headline(world, uid) == D("1000")
    assert await unscoped_holdings_sum(world, uid) == D("2000")


async def test_12_revalue_inside_the_scope_agrees_and_persists(world):
    from app.core.cas_scope import effective_scope, scoped_to
    from app.domains.portfolio.services.portfolio_service import (
        revalue_primary_portfolio_at_latest_nav,
    )

    uid = await new_user(world)
    await upload(world, uid, [_txn(Z, 300, "BUY", "1")])
    await upload(world, uid, [_txn(A, 300, "BUY", "10")])
    async with world() as db:
        await db.execute(
            text("UPDATE portfolios SET total_value = 2000 WHERE user_id = :u"),
            {"u": uid},
        )
        await db.commit()
    async with world() as db:
        snapshot_id = await effective_scope(db, uid)
        with scoped_to(snapshot_id):
            await revalue_primary_portfolio_at_latest_nav(db, uid)

    assert await headline(world, uid) == D("1000")


async def test_13_as_of_valuer_pins_the_statement(world):
    from app.domains.portfolio.services.networth.asof import compute_today_networth

    uid = await new_user(world)
    await upload(world, uid, [_txn(Z, 300, "BUY", "1")])
    await upload(world, uid, [_txn(A, 300, "BUY", "10"), _txn(B, 100, "BUY", "4")])
    async with world() as db:
        total, invested, gain = await compute_today_networth(db, uid)

    assert total == value(**{A: "10", B: "4"}) == D("1200")
    assert invested == D("1200")
    assert gain == D("0")


async def test_14_fully_redeemed_fund_contributes_nothing(world):
    uid = await new_user(world)
    await upload(
        world,
        uid,
        [
            _txn(A, 300, "BUY", "10"),
            _txn(A, 100, "SELL", "-10"),
            _txn(B, 200, "BUY", "20"),
        ],
    )
    await rebuild(world, uid)
    await daily(world)

    assert D((await row_for(world, uid)).total_value) == value(**{B: "20"})
    assert (await positions(world, uid))[A] == D("0")


async def test_15_positions_never_contain_a_superseded_fund(world):
    uid = await new_user(world)
    await upload(world, uid, [_txn(Z, 300, "BUY", "1"), _txn(C, 300, "BUY", "50")])
    await upload(world, uid, [_txn(A, 300, "BUY", "10")])
    await rebuild(world, uid)

    assert set(await positions(world, uid)) == {A}


async def test_16_supersede_replaces_the_series_wholesale(world):
    uid = await new_user(world)
    await upload(world, uid, [_txn(A, 300, "BUY", "10")])
    await rebuild(world, uid)
    assert (await state(world, uid)).first_date == _today() - timedelta(days=300)

    await upload(world, uid, [_txn(B, 100, "BUY", "20")])  # a corrected, narrower CAS
    await rebuild(world, uid)
    await daily(world)

    st = await state(world, uid)
    assert st.first_date == _today() - timedelta(days=100)
    assert st.row_count == 101
    assert D((await row_for(world, uid)).total_value) == value(**{B: "20"})
    assert await row_for(world, uid, _today() - timedelta(days=200)) is None


async def test_17_live_rebuild_job_is_skipped_by_the_daily_pass(world):
    from app.domains.portfolio.models.portfolio_networth_job import PortfolioNetworthJob

    uid = await new_user(world)
    await upload(world, uid, [_txn(A, 300, "BUY", "10")])
    await rebuild(world, uid)
    async with world() as db:
        await db.execute(
            text(
                "DELETE FROM user_portfolio_nav_history WHERE user_id = :u AND recorded_date = :d"
            ),
            {"u": uid, "d": _today()},
        )
        job = PortfolioNetworthJob(
            user_id=uid,
            status="running",
            phase="computing",
            progress_pct=50,
            trigger="manual",
        )
        db.add(job)
        await db.commit()
        job_id = job.id

    assert await daily(world) == 0
    assert await row_for(world, uid) is None

    async with world() as db:
        await db.execute(
            text("UPDATE portfolio_networth_jobs SET status = 'success' WHERE id = :j"),
            {"j": job_id},
        )
        await db.commit()
    assert await daily(world) == 1
    assert D((await row_for(world, uid)).total_value) == D("1000")


async def test_18_versioning_off_reads_every_row_and_never_crashes(world, monkeypatch):
    """The kill switch restores the pre-feature world: no statement filter at all."""
    from app.core import cas_scope

    uid = await new_user(world)
    await upload(world, uid, [_txn(Z, 300, "BUY", "1")])
    await upload(world, uid, [_txn(A, 300, "BUY", "10")])
    monkeypatch.setattr(cas_scope, "versioning_enabled", lambda: False)
    await rebuild(world, uid)
    await daily(world)

    assert D((await row_for(world, uid)).total_value) == D("2000")


async def test_19_forward_fill_reprices_each_missing_day_and_advances_state(world):
    from app.domains.portfolio.services.networth.daily import refresh_day

    uid = await new_user(world)
    await upload(world, uid, [_txn(A, 300, "BUY", "10")], holdings=[(A, "12", "100")])
    await rebuild(world, uid)
    today = _today()
    async with world() as db:
        await db.execute(
            text(
                "DELETE FROM user_portfolio_nav_history WHERE user_id = :u AND recorded_date > :d"
            ),
            {"u": uid, "d": today - timedelta(days=3)},
        )
        await db.execute(
            text(
                "UPDATE user_networth_series_state SET last_date = :d WHERE user_id = :u"
            ),
            {"u": uid, "d": today - timedelta(days=3)},
        )
        await db.commit()

    async with world() as db:
        for offset in (2, 1, 0):
            assert await refresh_day(db, today - timedelta(days=offset)) == 1

    for offset in (2, 1, 0):
        row = await row_for(world, uid, today - timedelta(days=offset))
        assert D(row.total_value) == D("1200")  # 10 units priced + the 200 anchor
    assert (await state(world, uid)).last_date == today


async def test_20_reconcile_reports_no_drift_for_a_multi_statement_user(world):
    """The tripwire compares against the ACTIVE holdings, not a column any unscoped
    writer can pollute — otherwise the correct series is what gets flagged."""
    from app.domains.portfolio.services.networth.daily import _reconcile

    uid = await new_user(world)
    await upload(world, uid, [_txn(Z, 300, "BUY", "1")])
    await upload(world, uid, [_txn(A, 300, "BUY", "10")])
    await rebuild(world, uid)
    await daily(world)
    async with world() as db:
        await db.execute(
            text("UPDATE portfolios SET total_value = 2000 WHERE user_id = :u"),
            {"u": uid},
        )
        await db.commit()
        assert await _reconcile(db, _today()) == 0


async def test_21_rewrite_day_replaces_every_users_today_row(world):
    """The repair path: drop the day fleet-wide and write it back from positions."""
    from app.domains.portfolio.services.networth.daily import rewrite_day

    u1, u2 = await new_user(world), await new_user(world)
    for uid in (u1, u2):
        await upload(world, uid, [_txn(Z, 300, "BUY", "1")])
        await upload(world, uid, [_txn(A, 300, "BUY", "10")])
        await rebuild(world, uid)
    async with world() as db:
        await db.execute(
            text(
                "UPDATE user_portfolio_nav_history SET total_value = 2000 WHERE recorded_date = :d"
            ),
            {"d": _today()},
        )
        await db.commit()

    async with world() as db:
        assert await rewrite_day(db, _today()) == (2, 2)

    for uid in (u1, u2):
        assert D((await row_for(world, uid)).total_value) == D("1000")


async def test_22_opening_balances_come_from_the_active_import_only(world):
    """A partial-period statement seeds each scheme from its opening balance. A
    superseded import's opening balance must not leak into the active build."""
    from app.domains.mutual_funds.models.enums import MfAaImportStatus
    from app.domains.mutual_funds.models.mf_aa_import import MfAaImport, MfAaSummary

    uid = await new_user(world)
    old = await upload(world, uid, [_txn(A, 300, "BUY", "10")])
    new = await upload(world, uid, [_txn(A, 300, "BUY", "10")])
    async with world() as db:
        for snap, opening in ((old, "100"), (new, "0")):
            imp = MfAaImport(
                user_id=uid,
                status=MfAaImportStatus.NORMALIZED,
                from_date="2025-01-01",
                to_date="2026-01-01",
                cas_upload_id=snap,
            )
            db.add(imp)
            await db.flush()
            db.add(
                MfAaSummary(
                    aa_import_id=imp.id,
                    scheme=A,
                    opening_bal=D(opening),
                    cost_value=D("1000"),
                    closing_balance=D("10"),
                )
            )
        await db.commit()

    result = await rebuild(world, uid)
    await daily(world)

    assert result.ledger_complete is True
    assert D((await row_for(world, uid)).total_value) == D("1000")
    assert (await positions(world, uid))[A] == D("10")


async def test_23_daily_adoption_rebuilds_run_a_few_at_a_time(world, monkeypatch):
    """Fifty rebuilds fired at once exhausted the connection pool on the first
    morning of the package ("QueuePool limit of size 10 overflow 10 reached"), so
    seven adoptions failed before loading a row. The sweep queues them all but runs
    only ``REBUILD_CONCURRENCY`` at a time."""
    from app.domains.portfolio.services.networth import builder, daily

    users = [await new_user(world) for _ in range(6)]
    today = _today()
    async with world() as db:
        for uid in users:
            # A series from the old builder: history rows, no state, no positions.
            await db.execute(
                text(
                    "INSERT INTO user_portfolio_nav_history "
                    "(id, user_id, recorded_date, total_value, total_invested, gain_percentage) "
                    "VALUES (gen_random_uuid(), :u, :d, 1000, 1000, 0)"
                ),
                {"u": uid, "d": today - timedelta(days=30)},
            )
        await db.commit()

    running = 0
    peak = 0
    seen: list[uuid.UUID] = []

    async def fake_rebuild(user_id, job_id, *, trigger):  # noqa: ARG001
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        seen.append(user_id)
        try:
            await asyncio.sleep(0.05)
        finally:
            running -= 1

    monkeypatch.setattr(builder, "rebuild_user_networth", fake_rebuild)

    async with world() as db:
        queued = await daily._queue_rebuilds(db, today - timedelta(days=7))
    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    await asyncio.gather(*pending)

    assert queued == 6
    assert sorted(map(str, seen)) == sorted(map(str, users))
    assert peak == daily.REBUILD_CONCURRENCY
