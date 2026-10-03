"""End to end against a real Postgres: CAS upload -> rebuild -> re-upload -> supersede -> daily -> HTTP.

Everything the unit tests cannot see lives here: the unnest inserts, the advisory lock,
``ON CONFLICT``, ``DISTINCT ON``/``ntile`` in the reader, the CAS read hook, the
BackgroundTasks hand-off in the upload route, and the purge of a superseded
statement's rows. It drives the REAL ingest with only the CAS Parser HTTP call stubbed
(the parsed-CAS dict is hand-built) and the S3 archive skipped.

Opt-in, because it needs a database::

    NETWORTH_PG_TEST=1 .venv/Scripts/python.exe -m pytest \\
        app/domains/portfolio/services/tests/test_networth_pg_e2e.py -q

It creates ONE throwaway user, never touches anyone else's rows, and purges that user
at the end through the same FK-graph walk the privacy erasure uses. The scheme codes
are real funds with deep NAV history on the dev database (UTI Nifty 50 Index Fund
``120716`` and SBI Large Cap ``119598``); the expected rupee figures are those funds'
published NAV on the transaction dates, so a change in the pricing rules — or in the
stored NAV — fails loudly rather than drifting.
"""

from __future__ import annotations

import os
import random
import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text

pytestmark = pytest.mark.skipif(
    os.environ.get("NETWORTH_PG_TEST") != "1",
    reason="needs a Postgres (set NETWORTH_PG_TEST=1; uses DATABASE_URL from .env)",
)

# ── real funds on the dev database ────────────────────────────────────────────
A_CODE, A_ISIN, A_NAME = "120716", "INF789F01XA0", "UTI Nifty 50 Index Fund - Direct Plan - Growth"
B_CODE, B_ISIN, B_NAME = "119598", "INF200K01QX4", "SBI Large Cap Fund - Direct Plan - Growth"

# Published NAV on the transaction dates (mf_nav_history on dev).
NAV = {
    (A_CODE, "2024-01-15"): Decimal("151.3762"),
    (A_CODE, "2024-06-14"): Decimal("161.6179"),
    (A_CODE, "2025-01-15"): Decimal("160.4686"),
    (B_CODE, "2024-01-15"): Decimal("86.2032"),
    (B_CODE, "2024-06-14"): Decimal("95.3229"),
    (B_CODE, "2025-01-15"): Decimal("94.8310"),
}


def _txn(day: str, kind: str, units: str, nav: str, amount: str) -> dict[str, Any]:
    return {
        "date": day,
        "description": kind.title(),
        "amount": amount,
        "units": units,
        "nav": nav,
        "balance": None,
        "type": kind,
        "dividend_rate": None,
        "stamp_duty": None,
        "stt": None,
    }


def _scheme(
    amfi: str,
    isin: str,
    name: str,
    txns: list[dict[str, Any]],
    *,
    close: str,
    nav: str,
    cost: str,
    statement_to: str,
) -> dict[str, Any]:
    value = (Decimal(close) * Decimal(nav)).quantize(Decimal("0.01"))
    return {
        "scheme": name,
        "isin": isin,
        "amfi": amfi,
        "type": "EQUITY",
        "rta_code": None,
        "advisor": None,
        "open": "0",
        "close": close,
        "valuation": {
            "value": str(value),
            "cost": cost,
            "nav": nav,
            "date": statement_to,
        },
        "transactions": txns,
    }


def _cas(frm: str, to: str, schemes: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "cas_type": "DETAILED",
        "file_type": "CAMS_KFINTECH",
        "statement_period": {"from": frm, "to": to},
        "investor_info": {
            "name": "Networth E2E",
            "email": None,
            "mobile": None,
            "address": None,
        },
        "folios": [
            {
                "folio": "E2E/0001",
                "amc": "Test AMC",
                "PAN": None,
                "registrar": "CAMS",
                "schemes": schemes,
            }
        ],
    }


A_BUY_1 = _txn("2024-01-15", "PURCHASE", "100", "151.3762", "15137.62")
A_BUY_2 = _txn("2024-06-14", "PURCHASE", "50", "161.6179", "8080.90")
A_BUY_3 = _txn("2025-01-15", "PURCHASE", "10", "160.4686", "1604.69")
A_BUY_4 = _txn("2025-02-14", "PURCHASE", "5", "200", "1000.00")  # no published NAV asserted
A_BUY_5 = _txn("2025-03-14", "PURCHASE", "5", "200", "1000.00")
B_BUY_1 = _txn("2024-01-15", "PURCHASE", "200", "86.2032", "17240.64")
# CAS prints redemption units and amounts as NEGATIVE numbers.
B_SELL_1 = _txn("2024-06-14", "REDEMPTION", "-50", "95.3229", "-4766.15")
B_SELL_2 = _txn("2025-01-15", "REDEMPTION", "-150", "94.8310", "-14224.65")


def statement_1() -> dict[str, Any]:
    to = "2024-12-31"
    return _cas(
        "2024-01-01",
        to,
        [
            _scheme(A_CODE, A_ISIN, A_NAME, [A_BUY_1, A_BUY_2], close="150", nav="161.6179", cost="23218.52", statement_to=to),
            _scheme(B_CODE, B_ISIN, B_NAME, [B_BUY_1, B_SELL_1], close="150", nav="95.3229", cost="12930.48", statement_to=to),
        ],
    )


def statement_2() -> dict[str, Any]:
    """The 'update CAMS' case: a superset ledger where B is now fully redeemed."""
    to = "2025-01-31"
    return _cas(
        "2024-01-01",
        to,
        [
            _scheme(A_CODE, A_ISIN, A_NAME, [A_BUY_1, A_BUY_2, A_BUY_3], close="160", nav="160.4686", cost="24823.21", statement_to=to),
            _scheme(B_CODE, B_ISIN, B_NAME, [B_BUY_1, B_SELL_1, B_SELL_2], close="0", nav="94.8310", cost="0", statement_to=to),
        ],
    )


def statement_n(extra: list[dict[str, Any]], close: str, to: str) -> dict[str, Any]:
    return _cas(
        "2024-01-01",
        to,
        [
            _scheme(A_CODE, A_ISIN, A_NAME, [A_BUY_1, A_BUY_2, A_BUY_3, *extra], close=close, nav="200", cost="0", statement_to=to),
        ],
    )


# ── fixtures ─────────────────────────────────────────────────────────────────


class _Queue:
    """Stand-in for fastapi.BackgroundTasks that just records what was queued."""

    def __init__(self) -> None:
        self.tasks: list[tuple] = []

    def add_task(self, func, /, *args, **kwargs) -> None:
        self.tasks.append((func, args, kwargs))

    async def run_all(self) -> None:
        while self.tasks:
            func, args, kwargs = self.tasks.pop(0)
            await func(*args, **kwargs)


@pytest_asyncio.fixture
async def pg():
    """Engine + listeners + schema patches; disposes the engine after the test."""
    # Every ORM class, not just the ones this file names: SQLAlchemy configures
    # mappers as a set, so importing User alone fails on the first relationship
    # pointing at a model nothing has imported yet.
    import app.all_models  # noqa: F401
    from app.core import cas_scope
    from app.core.database import (
        _get_session_factory,
        apply_postgres_schema_patches,
        dispose_engine,
    )

    cas_scope.install_cas_scope_listeners()
    # The DB is stamped at a lost alembic revision: startup DDL is how columns arrive.
    await apply_postgres_schema_patches()
    yield _get_session_factory()
    await dispose_engine()


@pytest_asyncio.fixture
async def user(pg):
    from app.domains.identity.models.user import User
    from app.domains.privacy.services.user_graph import delete_user_rows

    mobile = "9" + "".join(random.choice("0123456789") for _ in range(9))
    row = User(
        country_code="+91",
        mobile=mobile,
        phone=f"+91{mobile}",
        currency="INR",
        is_active=True,
        is_onboarding_complete=True,
        first_name="Networth",
        last_name="E2E",
    )
    async with pg() as db:
        db.add(row)
        await db.commit()
        uid = row.id
    try:
        yield uid
    finally:
        from app.core.cas_scope import set_scope

        set_scope(None)
        async with pg() as db:
            await delete_user_rows(db, uid, dry_run=False)
            await db.commit()


# ── helpers ──────────────────────────────────────────────────────────────────


async def _upload(pg, monkeypatch, user_id: uuid.UUID, parsed: dict[str, Any], nonce: int):
    """Drive the real ingest with the parser stubbed. Returns the ingest result."""
    from app.core.cas_scope import set_scope
    from app.domains.ingestion.services import cams_cas_ingest

    async def fake_parse(file_bytes, source_filename, password):  # noqa: ARG001
        return parsed

    async def no_archive(*args, **kwargs):  # noqa: ARG001
        return None

    monkeypatch.setattr(cams_cas_ingest, "_parse_cas_via_api", fake_parse)
    monkeypatch.setattr(cams_cas_ingest, "_archive_statement_pdf", no_archive)
    async with pg() as db:
        result = await cams_cas_ingest.ingest_cams_pdf(
            db,
            user_id,
            file_bytes=b"%PDF-1.4 e2e " + str(nonce).encode(),
            password="E2E",
            source_filename=f"e2e-{nonce}.pdf",
        )
        await db.commit()
    set_scope(None)
    assert result.status == "NORMALIZED", result
    return result


async def _upload_and_rebuild(pg, monkeypatch, user_id, parsed, nonce):
    from app.domains.portfolio.services.networth.trigger import (
        schedule_rebuild_after_cas,
    )

    result = await _upload(pg, monkeypatch, user_id, parsed, nonce)
    queue = _Queue()
    async with pg() as db:
        job_id = await schedule_rebuild_after_cas(
            db, queue, user_id, ingest_failed=False
        )
    assert job_id is not None and len(queue.tasks) == 1
    await queue.run_all()
    return result, job_id


async def _one(pg, sql: str, **params):
    async with pg() as db:
        return (await db.execute(text(sql), params)).first()


async def _all(pg, sql: str, **params):
    async with pg() as db:
        return (await db.execute(text(sql), params)).all()


async def _series_point(pg, user_id, day: str):
    return await _one(
        pg,
        "SELECT total_value, total_invested, gain_percentage FROM user_portfolio_nav_history "
        "WHERE user_id = :u AND recorded_date = :d",
        u=user_id,
        d=date.fromisoformat(day),
    )


async def _positions(pg, user_id) -> dict[str, Decimal]:
    rows = await _all(
        pg,
        "SELECT scheme_code, units FROM user_scheme_position WHERE user_id = :u",
        u=user_id,
    )
    return {r.scheme_code: Decimal(r.units) for r in rows}


async def _state(pg, user_id):
    return await _one(
        pg, "SELECT * FROM user_networth_series_state WHERE user_id = :u", u=user_id
    )


async def _job(pg, job_id):
    return await _one(pg, "SELECT * FROM portfolio_networth_jobs WHERE id = :j", j=job_id)


async def _latest_nav(pg, code: str) -> Decimal:
    row = await _one(
        pg,
        "SELECT nav FROM mf_nav_history WHERE scheme_code = :c AND nav > 0 "
        "ORDER BY nav_date DESC LIMIT 1",
        c=code,
    )
    return Decimal(row.nav)


def _today() -> date:
    from app.domains.portfolio.services.networth.clock import ist_today

    return ist_today()


# ── the journey ──────────────────────────────────────────────────────────────


async def test_upload_reupload_supersede_daily_and_http(pg, user, monkeypatch):
    user_id = user
    today = _today()

    # ── 1. first upload: series built from the ledger, priced at published NAV ──
    result1, job1 = await _upload_and_rebuild(pg, monkeypatch, user_id, statement_1(), 1)
    snap1 = result1.cas_upload_id
    job = await _job(pg, job1)
    assert job.status == "success", job.message
    assert job.trigger == "cas_upload"

    state = await _state(pg, user_id)
    assert state.first_date == date(2024, 1, 15)
    assert state.last_date == today
    assert state.row_count == (today - date(2024, 1, 15)).days + 1
    assert state.built_from_cas_upload_id == snap1
    assert state.ledger_complete is True
    assert state.degraded_schemes == 0
    # Holdings and ledger agree exactly, so the anchor is a no-op. This also pins the
    # rule that the rebuild prices the headline off STORED NAV, never a live fetch —
    # a fetch on a weekend would move the headline and leave a phantom anchor.
    assert Decimal(state.anchor_value) == 0
    assert Decimal(state.anchor_invested) == 0

    assert await _positions(pg, user_id) == {A_CODE: Decimal("150"), B_CODE: Decimal("150")}

    p = await _series_point(pg, user_id, "2024-01-15")
    assert (p.total_value, p.total_invested) == (Decimal("32378.26"), Decimal("32378.26"))
    assert p.gain_percentage == 0

    p = await _series_point(pg, user_id, "2024-06-14")
    # A: 150 x 161.6179 ; B: 150 x 95.3229 ; basis: A 23218.52 + B 17240.64 x 150/200
    assert p.total_value == Decimal("38541.12")
    assert p.total_invested == Decimal("36149.00")
    assert abs(p.gain_percentage - Decimal("6.6174")) < Decimal("0.0002")

    # Last point == positions x latest NAV == the dashboard headline.
    last = await _series_point(pg, user_id, today.isoformat())
    expected_today = (
        Decimal("150") * await _latest_nav(pg, A_CODE)
        + Decimal("150") * await _latest_nav(pg, B_CODE)
    ).quantize(Decimal("0.01"))
    assert last.total_value == expected_today
    headline = await _one(
        pg, "SELECT total_value FROM portfolios WHERE user_id = :u AND is_primary", u=user_id
    )
    assert abs(Decimal(headline.total_value) - expected_today) <= Decimal("1.00")

    # No rows outside the window, none duplicated.
    bounds = await _one(
        pg,
        "SELECT MIN(recorded_date) mn, MAX(recorded_date) mx, COUNT(*) n, COUNT(DISTINCT recorded_date) d "
        "FROM user_portfolio_nav_history WHERE user_id = :u",
        u=user_id,
    )
    assert (bounds.mn, bounds.mx, bounds.n) == (date(2024, 1, 15), today, bounds.d)

    # ── 2. 'update CAMS': the series is REPLACED and the old statement's rows go ──
    result2, job2 = await _upload_and_rebuild(pg, monkeypatch, user_id, statement_2(), 2)
    snap2 = result2.cas_upload_id
    assert snap2 != snap1
    assert (await _job(pg, job2)).status == "success"

    state = await _state(pg, user_id)
    assert state.built_from_cas_upload_id == snap2
    assert await _positions(pg, user_id) == {A_CODE: Decimal("160"), B_CODE: Decimal("0")}

    p = await _series_point(pg, user_id, "2025-01-15")
    assert p.total_value == Decimal("25674.98")  # 160 x 160.4686, B fully redeemed
    assert p.total_invested == Decimal("24823.21")
    # A superset ledger reproduces the earlier day exactly.
    assert (await _series_point(pg, user_id, "2024-06-14")).total_value == Decimal("38541.12")

    # The superseded statement's DIRECT data is deleted, not merely hidden ...
    for table in ("mf_transactions", "mf_aa_imports", "portfolio_holdings", "portfolio_allocations", "user_mf_latest_snapshot"):
        n = await _one(pg, f"SELECT COUNT(*) n FROM {table} WHERE cas_upload_id = :s", s=snap1)
        assert n.n == 0, f"{table} still holds rows of the superseded statement"
    # ... while the statement record itself stays, as the audit trail.
    old = await _one(pg, "SELECT status FROM cas_uploads WHERE id = :s", s=snap1)
    assert old.status == "superseded"
    # And the live statement's rows are all there.
    live = await _one(pg, "SELECT COUNT(*) n FROM mf_transactions WHERE cas_upload_id = :s", s=snap2)
    assert live.n == 6

    # ── 3. a statement landing MID-BUILD makes the worker run again under the NEW scope ──
    from app.domains.portfolio.services.networth import builder
    from app.domains.portfolio.services.networth.trigger import (
        schedule_rebuild_after_cas,
    )

    orig_build = builder.build_series
    landed: dict[str, Any] = {}

    async def build_then_land(db, uid, snapshot_id=None, *, job_id=None):
        if not landed:
            landed["snap"] = (
                await _upload(pg, monkeypatch, uid, statement_n([A_BUY_4], "165", "2025-02-28"), 3)
            ).cas_upload_id
            q = _Queue()
            async with pg() as db2:
                await schedule_rebuild_after_cas(db2, q, uid, ingest_failed=False)
            assert not q.tasks, "a second worker must never be queued while one runs"
        return await orig_build(db, uid, snapshot_id, job_id=job_id)

    monkeypatch.setattr(builder, "build_series", build_then_land)
    from app.domains.portfolio.services.networth import job as job_store

    async with pg() as db:
        job3, created = await job_store.create_job(db, user_id, trigger="manual", supersede=False)
    assert created
    await builder.rebuild_user_networth(user_id, job3.id, trigger="manual")
    monkeypatch.setattr(builder, "build_series", orig_build)

    row = await _job(pg, job3.id)
    assert row.status == "success"
    assert row.attempt == 2 and row.supersede_requested is False
    state = await _state(pg, user_id)
    assert state.built_from_cas_upload_id == landed["snap"], (
        "the re-run rebuilt from the statement the upload replaced"
    )
    assert await _positions(pg, user_id) == {A_CODE: Decimal("165")}

    # ── 4. the daily refresh re-prices today's point from positions ──
    from app.domains.portfolio.services.networth.daily import refresh_day

    async with pg() as db:
        await db.execute(
            text("UPDATE user_portfolio_nav_history SET total_value = 1 WHERE user_id = :u AND recorded_date = :d"),
            {"u": user_id, "d": today},
        )
        await db.commit()
        written = await refresh_day(db, today)
    assert written >= 1
    last = await _series_point(pg, user_id, today.isoformat())
    assert last.total_value == (Decimal("165") * await _latest_nav(pg, A_CODE)).quantize(Decimal("0.01"))
    assert last.total_invested == Decimal((await _state(pg, user_id)).last_invested)

    # ── 5. HTTP: the routes the dashboard calls, on the real app ──
    import httpx

    from app.core.security import create_access_token
    from app.main import app

    phone = (await _one(pg, "SELECT phone FROM users WHERE id = :u", u=user_id)).phone
    headers = {"Authorization": f"Bearer {create_access_token(user_id, phone)}"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://e2e"
    ) as client:
        r = await client.get("/api/v1/portfolio/nav-history", params={"horizon": "MAX"}, headers=headers)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["points"][0]["recorded_date"] == "2024-01-15"
        assert body["points"][-1]["recorded_date"] == today.isoformat()
        assert body["as_of"] == today.isoformat()
        assert body["is_stale"] is False
        assert body["ledger_complete"] is True
        assert body["current_value"] == float(last.total_value)
        assert r.headers.get("ETag")

        r = await client.get("/api/v1/portfolio/networth-history/status", headers=headers)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "success" and r.json()["has_history"] is True

        n_before = (await _one(pg, "SELECT COUNT(*) n FROM portfolio_networth_jobs WHERE user_id = :u", u=user_id)).n
        r = await client.post("/api/v1/portfolio/networth-history/build", headers=headers)
        assert r.status_code == 200, r.text
        n_after = (await _one(pg, "SELECT COUNT(*) n FROM portfolio_networth_jobs WHERE user_id = :u", u=user_id)).n
        assert n_after == n_before, "a rebuild seconds after the last one, with no new statement, must not queue"

        r = await client.get("/api/v1/portfolio/", headers=headers)
        assert r.status_code == 200, r.text
        assert abs(Decimal(str(r.json()["total_value"])) - last.total_value) <= Decimal("1.00")

        # The real upload route: parser stubbed, BackgroundTasks run inside the ASGI call.
        from app.domains.ingestion.services import cams_cas_ingest

        async def fake_parse(file_bytes, source_filename, password):  # noqa: ARG001
            return statement_n([A_BUY_4, A_BUY_5], "170", "2025-03-31")

        async def no_archive(*args, **kwargs):  # noqa: ARG001
            return None

        monkeypatch.setattr(cams_cas_ingest, "_parse_cas_via_api", fake_parse)
        monkeypatch.setattr(cams_cas_ingest, "_archive_statement_pdf", no_archive)
        r = await client.post(
            "/api/v1/mf-ingest/cams-pdf",
            headers=headers,
            files={"file": ("e2e-4.pdf", b"%PDF-1.4 e2e 4", "application/pdf")},
            data={"password": "E2E", "replace_existing": "false"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "NORMALIZED"

    active = await _one(pg, "SELECT id FROM cas_uploads WHERE user_id = :u AND status = 'active'", u=user_id)
    state = await _state(pg, user_id)
    assert state.built_from_cas_upload_id == active.id, "the upload route did not rebuild from the new statement"
    assert await _positions(pg, user_id) == {A_CODE: Decimal("170")}
    latest_job = await _one(
        pg, "SELECT status, trigger FROM portfolio_networth_jobs WHERE user_id = :u ORDER BY created_at DESC LIMIT 1", u=user_id
    )
    assert (latest_job.status, latest_job.trigger) == ("success", "cas_upload")
    gone = await _one(pg, "SELECT COUNT(*) n FROM mf_transactions WHERE user_id = :u AND cas_upload_id <> :a", u=user_id, a=active.id)
    assert gone.n == 0


async def test_failed_ingest_never_queues_and_empty_ledger_clears(pg, user):
    """No statement, no rows: the rebuild must clear cleanly (this was 'A transaction is already begun')."""
    from app.domains.portfolio.services.networth.builder import build_series
    from app.domains.portfolio.services.networth.trigger import (
        schedule_rebuild_after_cas,
    )

    q = _Queue()
    async with pg() as db:
        assert await schedule_rebuild_after_cas(db, q, user, ingest_failed=True) is None
    assert not q.tasks

    async with pg() as db:
        result = await build_series(db, user, None)
    assert result.days_written == 0
    assert (await _state(pg, user)).row_count == 0


async def test_a_series_without_state_is_a_rebuild_candidate(pg, user):
    """The migration path off the old builder.

    Pricing and staleness are both keyed on ``user_networth_series_state``, which only a
    rebuild writes — so every account carrying a series from the previous builder is
    priced by nothing and, before this, queued by nothing: their chart froze on whatever
    that builder last wrote, wrong values included. ``_STALE_USERS`` has to see them.

    Read-only on purpose: it runs the candidate query rather than ``_queue_rebuilds``,
    which would create job rows for real accounts on the shared dev database.
    """
    from app.domains.portfolio.services.networth.daily import _STALE_USERS

    yesterday = _today() - timedelta(days=1)
    async with pg() as db:
        await db.execute(
            text(
                "INSERT INTO user_portfolio_nav_history "
                "(id, user_id, recorded_date, total_value, total_invested, gain_percentage) "
                "VALUES (gen_random_uuid(), :u, :d, 8889458.91, 7347212.37, 20.9839)"
            ),
            {"u": user, "d": yesterday},
        )
        await db.commit()

    assert await _state(pg, user) is None, "precondition: the legacy shape has no state"

    async def candidates() -> set:
        rows = await _all(pg, str(_STALE_USERS), cutoff=_today(), limit=5000)
        return {r.user_id for r in rows}

    assert user in await candidates(), "a series with no state row must be adopted"

    # Once adopted, the state row itself decides: current means not a candidate.
    async with pg() as db:
        await db.execute(
            text(
                "INSERT INTO user_networth_series_state "
                "(user_id, first_date, last_date, row_count, last_invested) "
                "VALUES (:u, :d, :t, 1, 7347212.37)"
            ),
            {"u": user, "d": yesterday, "t": _today()},
        )
        await db.commit()

    assert user not in await candidates(), "a current state row must not be re-queued"
