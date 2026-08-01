"""A turn's user row must sort before its assistant row (incident 2026-07-25).

Both rows of a turn are inserted in one transaction, and ``created_at`` is
``server_default=func.now()`` — which in Postgres is the TRANSACTION timestamp.
So every turn's two rows carry byte-identical timestamps and ``ORDER BY
created_at`` alone is an ambiguous sort. When it resolved assistant-first, the
transcript showed an answer above its own question and the prompt history ended
with a dangling user message, which sent the portfolio_query agent down its
goal-planning guardrail.

These tests exercise the real SQL (sqlite), not a mocked session — the existing
``test_chat_context`` suite stubs ``db.execute``, so it cannot see an ORDER BY.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import selectinload

import app.all_models  # noqa: F401  -- registers FK target tables with Base.metadata
from app.domains.chat.models.chat import ChatMessage, ChatMessageRole, ChatSession
from app.domains.chat.services.chat_context import load_conversation_history

# One instant shared by both rows of a turn — what func.now() actually produces.
TURN_AT = datetime(2026, 7, 25, 8, 27, 54, 910656, tzinfo=timezone.utc)


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


async def _seed_turn(db: AsyncSession, *, physical_order: str) -> uuid.UUID:
    """One turn whose two rows share a timestamp, inserted in the given order.

    The ordering must hold whichever way the rows physically land, so every test
    runs under both. This matters: the two production paths sort in opposite
    directions (the display path ASC, the prompt path DESC-then-reversed), so a
    given physical order breaks exactly one of them and hides the other.
    """
    session = ChatSession(id=uuid.uuid4(), user_id=uuid.uuid4(), title="t")
    db.add(session)
    rows = {
        "user": ChatMessage(
            session_id=session.id,
            role=ChatMessageRole.user,
            content="question",
            created_at=TURN_AT,
        ),
        "assistant": ChatMessage(
            session_id=session.id,
            role=ChatMessageRole.assistant,
            content="answer",
            intent="portfolio_query",
            created_at=TURN_AT,
        ),
    }
    for role in physical_order.split(","):
        db.add(rows[role])
        await db.flush()  # pin insertion order; the UoW does not preserve add() order
    await db.commit()
    return session.id


# Production inserts user-then-assistant (chat_router.send_message), but the
# unit of work does not guarantee that reaches storage in that order.
PHYSICAL_ORDERS = ["user,assistant", "assistant,user"]


@pytest.mark.asyncio
@pytest.mark.parametrize("physical_order", PHYSICAL_ORDERS)
async def test_prompt_history_puts_question_before_its_answer(
    db_session, physical_order
):
    """load_conversation_history must never hand an LLM a dangling user message."""
    session_id = await _seed_turn(db_session, physical_order=physical_order)

    history = await load_conversation_history(session_id, db_session)

    assert [m["role"] for m in history] == ["user", "assistant"]
    assert [m["content"] for m in history] == ["question", "answer"]


@pytest.mark.asyncio
@pytest.mark.parametrize("physical_order", PHYSICAL_ORDERS)
async def test_displayed_transcript_puts_question_before_its_answer(
    db_session, physical_order
):
    """ChatSession.messages must never render an answer above its own question."""
    session_id = await _seed_turn(db_session, physical_order=physical_order)

    result = await db_session.execute(
        select(ChatSession)
        .where(ChatSession.id == session_id)
        .options(selectinload(ChatSession.messages))
    )
    session = result.scalar_one()

    assert [m.role for m in session.messages] == [
        ChatMessageRole.user,
        ChatMessageRole.assistant,
    ]
