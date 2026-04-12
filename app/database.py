"""Async SQLAlchemy engine, session factory, and declarative ``Base``.

Reads the database URL via ``app.config`` (``DATABASE_URL`` or ``POSTGRES_*`` / ``DB_*``
components built with ``sqlalchemy.engine.url.URL`` for safe passwords, e.g. RDS), normalizes
it for ``asyncpg``, and exposes
``get_db`` as an async generator dependency for FastAPI routes. ``create_all_tables`` /
``dispose_engine`` support lifespan management from ``main``.
"""


from __future__ import annotations

from typing import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

from app.config import get_settings


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
            {"poolclass": NullPool} if is_local else {"pool_pre_ping": True, "pool_recycle": 300}
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
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def create_all_tables() -> None:
    engine = _get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def dispose_engine() -> None:
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None
