"""load_conversation_history emits whole turns only (incident 2026-07-25).

A turn's two rows are written together, so history is really a list of
*exchanges*, not loose messages. Returning loose messages let a half-turn reach
an LLM: on 2026-07-25 the prompt history ended with a user message that looked
unanswered, and the portfolio_query agent answered THAT instead of the question
actually asked. Pairing at the load point makes the dangling-question state
unrepresentable rather than merely unlikely.

Failed turns are the other source of half-turns — the dev DB holds 42 user-only
and 43 assistant-only rows from turns that errored mid-flight.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

import app.all_models  # noqa: F401  -- registers FK target tables with Base.metadata
from app.domains.chat.models.chat import ChatMessage, ChatMessageRole, ChatSession
from app.domains.chat.services.chat_context import load_conversation_history

T0 = datetime(2026, 7, 12, 9, 3, 21, tzinfo=timezone.utc)


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        # Base.metadata.create_all FAILS on sqlite (unrelated Postgres ARRAY
        # model) — create only the tables under test.
        await conn.run_sync(ChatSession.__table__.create)
        await conn.run_sync(ChatMessage.__table__.create)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        try:
            yield session
        finally:
            await session.rollback()


async def _seed(db: AsyncSession, rows: list[tuple[str, str, datetime]]) -> uuid.UUID:
    """Seed (role, content, created_at) rows into one session, in order."""
    session = ChatSession(id=uuid.uuid4(), user_id=uuid.uuid4(), title="t")
    db.add(session)
    for role, content, at in rows:
        db.add(
            ChatMessage(
                session_id=session.id,
                role=ChatMessageRole(role),
                content=content,
                intent="portfolio_query" if role == "assistant" else None,
                created_at=at,
            )
        )
        await db.flush()
    await db.commit()
    return session.id


@pytest.mark.asyncio
async def test_unanswered_user_message_is_not_surfaced(db_session):
    """The exact failure mode: a half-turn whose reply never landed."""
    session_id = await _seed(
        db_session,
        [
            ("user", "answered question", T0),
            ("assistant", "the answer", T0),
            # Turn errored — the reply was never written.
            ("user", "Wil I be able to meet my goals?", T0 + timedelta(minutes=5)),
        ],
    )

    history = await load_conversation_history(session_id, db_session)

    assert [m["content"] for m in history] == ["answered question", "the answer"]


@pytest.mark.asyncio
async def test_orphaned_error_reply_is_not_surfaced(db_session):
    """An assistant-only row (brain rolled back the user message) is not context."""
    session_id = await _seed(
        db_session,
        [
            ("assistant", "Sorry — I couldn't process your request.", T0),
            ("user", "a real question", T0 + timedelta(minutes=5)),
            ("assistant", "a real answer", T0 + timedelta(minutes=5)),
        ],
    )

    history = await load_conversation_history(session_id, db_session)

    assert [m["content"] for m in history] == ["a real question", "a real answer"]


@pytest.mark.asyncio
async def test_each_entry_carries_the_turn_timestamp(db_session):
    """asked_at lets prompt builders tell a live thread from a resumed one."""
    session_id = await _seed(
        db_session,
        [("user", "q", T0), ("assistant", "a", T0)],
    )

    history = await load_conversation_history(session_id, db_session)

    asked_at = [m["asked_at"] for m in history]
    assert asked_at[0] == asked_at[1], "one turn is one instant, shared by both rows"
    # sqlite has no tz-aware DateTime and drops tzinfo on round-trip; Postgres
    # (the column is DateTime(timezone=True)) returns it aware.
    assert asked_at[0].replace(tzinfo=None) == T0.replace(tzinfo=None)
