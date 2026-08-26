"""Async SQLAlchemy engine, session factory, and declarative ``Base``.

Reads the database URL via ``app.config`` (``DATABASE_URL`` or ``POSTGRES_*`` / ``DB_*``
components built with ``sqlalchemy.engine.url.URL`` for safe passwords, e.g. RDS), normalizes
it for ``asyncpg``, and exposes
``get_db`` as an async generator dependency for FastAPI routes. ``create_all_tables`` /
``dispose_engine`` support lifespan management from ``main``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator

from sqlalchemy import JSON, text
from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

from sqlalchemy.engine import make_url

from app.core.config import get_settings

logger = logging.getLogger(__name__)

# How many times to retry establishing the initial DB connection when it fails
# for a *transient* reason (DNS hiccup -> getaddrinfo failed, connection
# reset/refused, server still warming up). Each retry waits a short, growing
# backoff. A hard failure (bad password, host genuinely gone) still surfaces
# after the attempts are exhausted.
_CONNECT_RETRIES = 5
_CONNECT_BACKOFF_S = 0.5
# Per-attempt connect timeout (TCP + TLS handshake + auth). Keeps a stalled
# handshake from hanging the request for asyncpg's 60s default before retrying.
_CONNECT_TIMEOUT_S = 10.0

# Substrings that mark a connection error as transient and worth retrying.
_TRANSIENT_CONNECT_MARKERS = (
    "getaddrinfo failed",
    "name or service not known",
    "temporary failure in name resolution",
    "connection refused",
    "connection reset",
    "connection was closed",
    "connection timed out",
    "timeout expired",
    "the remote computer refused the network connection",
    "server closed the connection",
    # Windows WSAEACCES (10013): the OS sporadically denies the outbound socket
    # — typically when the random local source port collides with a reserved
    # range (Hyper-V/WSL/Docker) or a security product briefly intercepts. The
    # next attempt grabs a different port and succeeds, so treat it as transient.
    "forbidden by its access permissions",
    "an attempt was made to access a socket",
    "10013",
)


def _is_transient_connect_error(exc: BaseException) -> bool:
    """True if ``exc`` (or any cause) looks like a retryable connection blip."""
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        # A connect/TLS-handshake timeout is retryable, but it surfaces as a bare
        # TimeoutError (asyncio.TimeoutError aliases the builtin on 3.11+) with an
        # empty message, so match the type — the text markers below would miss it.
        if isinstance(cur, (asyncio.TimeoutError, TimeoutError)):
            return True
        msg = str(cur).lower()
        if any(marker in msg for marker in _TRANSIENT_CONNECT_MARKERS):
            return True
        cur = cur.__cause__ or cur.__context__
    return False


@compiles(JSONB, "sqlite")
def _jsonb_renders_as_json_on_sqlite(type_, compiler, **kw):
    """Local-dev only: render JSONB as JSON on SQLite. Postgres path unchanged."""
    return compiler.visit_JSON(JSON(), **kw)


class Base(DeclarativeBase):
    pass


_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def _get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        url = get_settings().get_database_url()
        is_local = "localhost" in url or "127.0.0.1" in url
        if is_local:
            engine_kw: dict = {"poolclass": NullPool}
        else:
            # Bound how long a single connect (TCP + TLS handshake + auth) may
            # take. asyncpg's default is 60s, which turns a transient handshake
            # stall — common on NAT64 / IPv6-mostly networks against RDS — into a
            # 60s request hang then a 500. A short timeout fails fast so
            # ``_connect_with_retry`` can retry and self-heal (a healthy connect
            # here completes in ~2s).
            engine_kw = {
                "pool_pre_ping": True,
                "pool_recycle": 300,
                # Explicit pool sizing (previously SQLAlchemy defaults: 5 + 10 = 15).
                # This single uvicorn instance shares one pool across request handlers,
                # the in-process APScheduler jobs, and net-worth backfills, so give some
                # headroom — but keep it modest: prozpr-dev is a db.t3.micro
                # (max_connections ~112) and every open connection costs RAM on a 1 GiB
                # DB. 20 max stays well under the DB limit with room for other clients.
                "pool_size": 10,
                "max_overflow": 10,
                "pool_timeout": 30,
                "connect_args": {"timeout": _CONNECT_TIMEOUT_S},
            }
        _engine = create_async_engine(url, **engine_kw)
    return _engine


def _get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            _get_engine(), expire_on_commit=False, class_=AsyncSession
        )
    return _session_factory


async def get_db() -> AsyncIterator[AsyncSession]:
    factory = _get_session_factory()
    async with factory() as session:
        # Establish the connection up front with a short retry so a transient
        # DNS/network blip (e.g. intermittent "getaddrinfo failed" against RDS)
        # self-heals instead of failing the request outright. Non-transient
        # errors (bad credentials, host truly gone) raise on the last attempt.
        await _connect_with_retry(session)
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def _connect_with_retry(session: AsyncSession) -> None:
    """Acquire the session's DB connection, retrying transient failures."""
    last_exc: BaseException | None = None
    for attempt in range(1, _CONNECT_RETRIES + 1):
        try:
            await session.connection()
            return
        except (OperationalError, InterfaceError, DBAPIError, OSError) as exc:
            last_exc = exc
            if attempt >= _CONNECT_RETRIES or not _is_transient_connect_error(exc):
                raise
            delay = _CONNECT_BACKOFF_S * attempt
            logger.warning(
                "DB connect attempt %d/%d failed transiently (%s); retrying in %.1fs",
                attempt,
                _CONNECT_RETRIES,
                exc,
                delay,
            )
            # Drop the half-open connection/transaction before retrying so the
            # next attempt starts clean.
            try:
                await session.rollback()
            except Exception:
                pass
            await asyncio.sleep(delay)
    if last_exc is not None:  # pragma: no cover - loop always raises or returns
        raise last_exc


async def create_all_tables() -> None:
    engine = _get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def apply_postgres_schema_patches() -> None:
    """Idempotent DDL for ORM/DB drift (e.g. RDS created before payload columns existed).

    Safe to run every startup: ``IF NOT EXISTS`` only.
    """
    parsed = make_url(get_settings().get_database_url())
    if not str(parsed.drivername).startswith("postgresql"):
        return

    engine = _get_engine()
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "ALTER TABLE chat_ai_module_runs ADD COLUMN IF NOT EXISTS input_payload JSONB"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE chat_ai_module_runs ADD COLUMN IF NOT EXISTS output_payload JSONB"
            )
        )
        await conn.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS ix_chat_ai_module_runs_session_module_created
                ON chat_ai_module_runs (session_id, module, created_at DESC)
                WHERE output_payload IS NOT NULL
                """
            )
        )
        # ORM/column drift: User.cams_skipped_at — set when the user picks
        # "I'll do this later" on the onboarding CAMS step, so the resume
        # resolver stops sending them back to /cams-upload.
        await conn.execute(
            text(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS cams_skipped_at "
                "TIMESTAMP WITH TIME ZONE"
            )
        )
        # ORM/column drift: User.pin_reset_* — forgot-PIN reset codes emailed
        # via Resend. The code is stored hashed with an expiry and a wrong-guess
        # counter; all three are cleared once a reset succeeds.
        await conn.execute(
            text(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS pin_reset_code_hash "
                "VARCHAR(255)"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS pin_reset_expires_at "
                "TIMESTAMP WITH TIME ZONE"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS pin_reset_attempts "
                "SMALLINT NOT NULL DEFAULT 0"
            )
        )
        # ORM/column drift: User.sensitive_change_* — the parked email/PAN edit
        # awaiting a step-up code. See /auth/me/sensitive/* and the model.
        await conn.execute(
            text(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS sensitive_change_field "
                "VARCHAR(32)"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS sensitive_change_value "
                "VARCHAR(320)"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS sensitive_change_code_hash "
                "VARCHAR(255)"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS sensitive_change_expires_at "
                "TIMESTAMP WITH TIME ZONE"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS sensitive_change_attempts "
                "SMALLINT NOT NULL DEFAULT 0"
            )
        )
        # ORM/column drift: ChatSession.rating — user's 1–5 rating of Pi, one per
        # conversation. Added after the table first shipped.
        await conn.execute(
            text("ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS rating SMALLINT")
        )
        # ORM/column drift: MfFundMetadata.isin* (see alembic f1a2b3c4d5e6)
        await conn.execute(
            text(
                "ALTER TABLE mf_fund_metadata ADD COLUMN IF NOT EXISTS isin VARCHAR(12)"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE mf_fund_metadata ADD COLUMN IF NOT EXISTS isin_div_reinvest VARCHAR(12)"
            )
        )
        await conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_mf_fund_metadata_isin ON mf_fund_metadata (isin)"
            )
        )
        await conn.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_mf_fund_metadata_isin_notnull "
                "ON mf_fund_metadata (isin) WHERE isin IS NOT NULL"
            )
        )
        # Perf: the rebalancing input builder looks NAV + metadata up by
        # ``mf_nav_history.isin`` (WHERE isin IN (...)). That column was
        # unindexed, forcing a sequential scan over the full daily-NAV history
        # on every rebalance. Index it so the lookup is an index scan.
        await conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_mf_nav_history_isin ON mf_nav_history (isin)"
            )
        )
        # ORM/column drift: IssueReport.source_detail added after the table first shipped.
        await conn.execute(
            text(
                "ALTER TABLE issue_reports ADD COLUMN IF NOT EXISTS source_detail VARCHAR(100)"
            )
        )
        # ORM/column drift (alembic b7d4e2f1a9c3): per-goal monthly SIP and the
        # current MF portfolio corpus that feeds the cashflow starting corpus.
        await conn.execute(
            text(
                "ALTER TABLE goals ADD COLUMN IF NOT EXISTS monthly_contribution NUMERIC(18,2)"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE personal_finance_profiles "
                "ADD COLUMN IF NOT EXISTS current_portfolio_corpus NUMERIC(18,2)"
            )
        )
        # ORM/column drift (alembic f5c2a1b3d8e7): listed equities / direct shares,
        # stored apart from financial_assets (now "cash & debt") and summed into the
        # cashflow corpus. Patched here so DBs on the divergent alembic head self-heal.
        await conn.execute(
            text(
                "ALTER TABLE personal_finance_profiles "
                "ADD COLUMN IF NOT EXISTS equity_shares NUMERIC(18,2)"
            )
        )
        # ORM/column drift: UserCurrentProperty.mortgage_balance (outstanding loan
        # amount collected in onboarding; added after the table first shipped).
        await conn.execute(
            text(
                "ALTER TABLE user_current_properties "
                "ADD COLUMN IF NOT EXISTS mortgage_balance NUMERIC(18,2)"
            )
        )
        # ORM/column drift: RiskProfile.investment_focus — the second behavioural
        # question (investment focus / risk appetite) collected on profile/complete,
        # which previously had no column and was silently dropped.
        await conn.execute(
            text(
                "ALTER TABLE risk_profiles "
                "ADD COLUMN IF NOT EXISTS investment_focus TEXT"
            )
        )
        # The behavioural-question answers store the full option sentence (often
        # >100 chars), so widen the original VARCHAR(100) columns to TEXT to avoid
        # "value too long for type character varying(100)" on save.
        for _col in ("investment_experience", "investment_focus", "drop_reaction"):
            await conn.execute(
                text(f"ALTER TABLE risk_profiles ALTER COLUMN {_col} TYPE TEXT")
            )
        # Goals: keep legacy + cashflow columns in sync (all nullable; skip missing cols).
        goal_cols = {
            row[0]
            for row in (
                await conn.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema = 'public' AND table_name = 'goals'"
                    )
                )
            ).fetchall()
        }
        if "name" in goal_cols and "goal_name" in goal_cols:
            await conn.execute(
                text(
                    "UPDATE goals SET goal_name = name "
                    "WHERE goal_name IS NULL AND name IS NOT NULL AND TRIM(name) <> ''"
                )
            )
            await conn.execute(
                text(
                    "UPDATE goals SET name = goal_name "
                    "WHERE name IS NULL AND goal_name IS NOT NULL AND TRIM(goal_name) <> ''"
                )
            )
        if "present_value_amount" in goal_cols:
            pv_sources = [
                c
                for c in ("goal_value_pv", "target_pv", "amount_needed")
                if c in goal_cols
            ]
            if pv_sources:
                coalesce = (
                    "COALESCE(present_value_amount, " + ", ".join(pv_sources) + ")"
                )
                await conn.execute(
                    text(
                        f"UPDATE goals SET present_value_amount = {coalesce} WHERE present_value_amount IS NULL"
                    )
                )
        if "goal_value_pv" in goal_cols and "present_value_amount" in goal_cols:
            await conn.execute(
                text(
                    """
                    UPDATE goals SET goal_value_pv = COALESCE(goal_value_pv, present_value_amount)
                    WHERE goal_value_pv IS NULL AND present_value_amount IS NOT NULL
                    """
                )
            )
        if "target_date" in goal_cols and "goal_date" in goal_cols:
            await conn.execute(
                text(
                    "UPDATE goals SET target_date = goal_date "
                    "WHERE target_date IS NULL AND goal_date IS NOT NULL"
                )
            )
            await conn.execute(
                text(
                    "UPDATE goals SET goal_date = target_date "
                    "WHERE goal_date IS NULL AND target_date IS NOT NULL"
                )
            )
        # FP execution: KYC readiness columns on fp_exec_accounts (the table
        # itself comes from create_all; these ALTERs cover a pre-existing table
        # created before the KYC columns were added).
        for ddl in (
            "ALTER TABLE IF EXISTS fp_exec_accounts "
            "ADD COLUMN IF NOT EXISTS kyc_status VARCHAR(20) NOT NULL DEFAULT 'pending'",
            "ALTER TABLE IF EXISTS fp_exec_accounts "
            "ADD COLUMN IF NOT EXISTS kyc_pv_id VARCHAR(80)",
            "ALTER TABLE IF EXISTS fp_exec_accounts "
            "ADD COLUMN IF NOT EXISTS kyc_checked_at TIMESTAMPTZ",
            "ALTER TABLE IF EXISTS fp_exec_accounts "
            "ADD COLUMN IF NOT EXISTS raw JSON",
            # The account row is now a shell created at signup — FP-side ids
            # arrive later (post-KYC), so the v1 NOT NULLs must go.
            "ALTER TABLE IF EXISTS fp_exec_accounts "
            "ALTER COLUMN fp_investor_id DROP NOT NULL",
            "ALTER TABLE IF EXISTS fp_exec_accounts "
            "ALTER COLUMN fp_investment_account_id DROP NOT NULL",
            "ALTER TABLE IF EXISTS fp_exec_accounts "
            "ALTER COLUMN holder_name DROP NOT NULL",
            "ALTER TABLE IF EXISTS fp_exec_accounts "
            "ALTER COLUMN pan DROP NOT NULL",
            "ALTER TABLE IF EXISTS fp_exec_accounts "
            "ALTER COLUMN bank_account_masked DROP NOT NULL",
        ):
            await conn.execute(text(ddl))

        # DPDP erasure: soft-delete columns on users. The account stops
        # authenticating the moment `deleted_at` is set (app/core/dependencies.py)
        # and the purge job picks it up once `deletion_scheduled_for` passes.
        await conn.execute(
            text(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS deleted_at "
                "TIMESTAMP WITH TIME ZONE"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS deletion_scheduled_for "
                "TIMESTAMP WITH TIME ZONE"
            )
        )
        await conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_users_deletion_scheduled_for "
                "ON users (deletion_scheduled_for) WHERE deleted_at IS NOT NULL"
            )
        )

        # DPDP: fp_exec_accounts.raw / fp_exec_orders.raw now carry Fernet
        # ciphertext (app/core/encrypted_types.EncryptedJSON), not JSON. They
        # hold the verbatim third-party payload — PAN, date of birth, gender,
        # income band and the unmasked bank account — which is exactly what a
        # database dump or an RDS snapshot would otherwise expose in the clear.
        #
        # USING raw::text keeps every existing row: the column becomes text
        # holding the old JSON, and EncryptedJSON's reader accepts unprefixed
        # values, so historical rows keep working and re-encrypt on next write.
        for _tbl in ("fp_exec_accounts", "fp_exec_orders"):
            await conn.execute(
                text(
                    f"ALTER TABLE IF EXISTS {_tbl} "
                    f"ALTER COLUMN raw TYPE TEXT USING raw::text"
                )
            )
    logger.info(
        "Postgres schema patches applied (chat_ai_module_runs, mf_fund_metadata, goals backfill, fp_exec_accounts kyc, fp raw encrypted-at-rest)"
    )


async def dispose_engine() -> None:
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None
