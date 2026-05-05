"""build_turn_context loads awaiting_save from chat_session_state."""

from __future__ import annotations

import uuid
from datetime import date

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.models  # noqa: F401  -- registers all ORM tables
from app.database import Base
from app.models.chat import ChatSession
from app.models.chat_session_state import ChatSessionState
from app.models.user import User
from app.services.chat_core.turn_context import build_turn_context
from app.services.chat_core.types import ChatTurnInput


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        try:
            yield session
        finally:
            await session.rollback()
    await engine.dispose()


@pytest_asyncio.fixture
async def fixture_user_and_session(db_session: AsyncSession):
    suffix = uuid.uuid4().hex[:8]
    user = User(
        id=uuid.uuid4(),
        email=f"awaiting_save_test_{suffix}@example.com",
        country_code="+91",
        mobile="9999999999",
        phone=f"+91-9999{suffix}",
        date_of_birth=date(1990, 1, 1),
    )
    db_session.add(user)
    await db_session.flush()
    session = ChatSession(user_id=user.id)
    db_session.add(session)
    await db_session.flush()
    return user, session


def _make_turn(user, session, db) -> ChatTurnInput:
    return ChatTurnInput(
        user_ctx=user,
        user_question="save it",
        conversation_history=[],
        client_context=None,
        session_id=session.id,
        db=db,
        user_id=user.id,
    )


@pytest.mark.asyncio
async def test_build_turn_context_loads_awaiting_save_true(
    db_session: AsyncSession, fixture_user_and_session,
):
    user, session = fixture_user_and_session
    db_session.add(ChatSessionState(session_id=session.id, awaiting_save=True))
    await db_session.flush()

    ctx = await build_turn_context(_make_turn(user, session, db_session))
    assert ctx.awaiting_save is True


@pytest.mark.asyncio
async def test_build_turn_context_awaiting_save_defaults_false(
    db_session: AsyncSession, fixture_user_and_session,
):
    user, session = fixture_user_and_session
    # No ChatSessionState row inserted.

    ctx = await build_turn_context(_make_turn(user, session, db_session))
    assert ctx.awaiting_save is False
