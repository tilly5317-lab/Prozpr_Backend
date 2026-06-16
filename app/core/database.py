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
_CONNECT_RETRIES = 3
_CONNECT_BACKOFF_S = 0.5

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
        engine_kw: dict = (
            {"poolclass": NullPool}
            if is_local
            else {"pool_pre_ping": True, "pool_recycle": 300}
        )
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
        # ORM/column drift: UserCurrentProperty.mortgage_balance (outstanding loan
        # amount collected in onboarding; added after the table first shipped).
        await conn.execute(
            text(
                "ALTER TABLE user_current_properties "
                "ADD COLUMN IF NOT EXISTS mortgage_balance NUMERIC(18,2)"
            )
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
    logger.info(
        "Postgres schema patches applied (chat_ai_module_runs, mf_fund_metadata, goals backfill)"
    )


async def dispose_engine() -> None:
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None
