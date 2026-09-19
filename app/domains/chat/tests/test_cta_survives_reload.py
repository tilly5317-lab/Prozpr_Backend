"""A reply's CTA must survive reopening the session.

The CTA a reply offered used to ride the send-response envelope only, so the
"Open preferences" pill and the "Add CAMS statement" card lived exactly as long
as the chat screen stayed mounted — navigating to the
preferences page and back rebuilt the conversation from the message rows, which
carried no CTA state, and both controls were gone.

The CAS card carries one asymmetry the pill does not: the value records that
holdings were missing WHEN THE TURN RAN, but the card asks a question about the
customer's state NOW. Once a statement is in, the ask is answered — the live
path already clears the card off every message on a successful upload, so the
history read must not resurrect it.
"""

from __future__ import annotations

import uuid

import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import selectinload

import app.all_models  # noqa: F401  -- registers FK target tables with Base.metadata
from app.domains.chat.models.chat import (
    CTA_ADD_CAMS,
    CTA_PREFERENCES,
    ChatMessage,
    ChatMessageRole,
    ChatSession,
)
from app.domains.chat.routers import chat_router

USER_ID = uuid.UUID("a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d")


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


async def _seed(db: AsyncSession, **fields) -> ChatSession:
    """One session: a user turn, then an assistant reply carrying `fields`."""
    session = ChatSession(user_id=USER_ID, title="Preferences")
    db.add(session)
    await db.flush()
    db.add(
        ChatMessage(
            session_id=session.id,
            role=ChatMessageRole.user,
            content="What preferences have I set?",
        )
    )
    db.add(
        ChatMessage(
            session_id=session.id,
            role=ChatMessageRole.assistant,
            content="Your investment preferences live on your preferences page.",
            intent="portfolio_query",
            **fields,
        )
    )
    await db.commit()
    return (
        await db.execute(
            select(ChatSession)
            .options(selectinload(ChatSession.messages))
            .where(ChatSession.id == session.id)
        )
    ).scalar_one()


async def test_the_cta_round_trips_through_the_message_row(db_session):
    """Persisted, not turn-only — the whole point of the column."""
    session = await _seed(db_session, cta=CTA_PREFERENCES)

    assistant = [m for m in session.messages if m.role == ChatMessageRole.assistant][0]
    assert assistant.cta == CTA_PREFERENCES


async def test_a_plain_reply_defaults_to_no_cta(db_session):
    """Rows written before this shipped, and every ordinary turn, carry none."""
    session = await _seed(db_session)

    assert all(m.cta is None for m in session.messages)


async def test_history_returns_the_preferences_pill(db_session, monkeypatch):
    """Reopening the session re-renders the pill that belonged to the reply."""
    session = await _seed(db_session, cta=CTA_PREFERENCES)

    out = await chat_router._history_messages(db_session, USER_ID, session)

    assert [m.cta for m in out] == [None, CTA_PREFERENCES]


async def test_a_pointer_never_goes_stale(db_session, monkeypatch):
    """Unlike the CAS card, the pill is not gated on the customer's state.

    Holdings present or not, "your preferences live on that page" stays true —
    so the pill must not borrow the card's suppression.
    """
    session = await _seed(db_session, cta=CTA_PREFERENCES)

    async def _has_holdings(db, user_id):
        return True

    monkeypatch.setattr(chat_router, "has_mf_holdings", _has_holdings)
    out = await chat_router._history_messages(db_session, USER_ID, session)

    assert any(m.cta == CTA_PREFERENCES for m in out)


async def test_the_cas_card_survives_while_holdings_are_still_missing(
    db_session, monkeypatch
):
    session = await _seed(db_session, cta=CTA_ADD_CAMS)

    async def _no_holdings(db, user_id):
        return False

    monkeypatch.setattr(chat_router, "has_mf_holdings", _no_holdings)
    out = await chat_router._history_messages(db_session, USER_ID, session)

    assert any(m.cta == CTA_ADD_CAMS for m in out)


async def test_the_cas_card_is_dropped_once_the_statement_is_in(
    db_session, monkeypatch
):
    """The regression this gate exists to prevent: re-asking for a CAS upload
    the customer already completed, every time they reopen the chat."""
    session = await _seed(db_session, cta=CTA_ADD_CAMS)

    async def _has_holdings(db, user_id):
        return True

    monkeypatch.setattr(chat_router, "has_mf_holdings", _has_holdings)
    out = await chat_router._history_messages(db_session, USER_ID, session)

    assert not any(m.cta == CTA_ADD_CAMS for m in out)


async def test_a_failed_holdings_check_suppresses_the_card(db_session, monkeypatch):
    """Fail toward silence: losing a shortcut beats re-asking someone who
    already uploaded. (The live path still raises the card on the next turn.)"""
    session = await _seed(db_session, cta=CTA_ADD_CAMS)

    async def _boom(db, user_id):
        raise RuntimeError("db hiccup")

    monkeypatch.setattr(chat_router, "has_mf_holdings", _boom)
    out = await chat_router._history_messages(db_session, USER_ID, session)

    assert not any(m.cta == CTA_ADD_CAMS for m in out)


async def test_no_holdings_query_runs_when_no_message_raised_the_card(
    db_session, monkeypatch
):
    """The gate costs one query per session load; don't pay it for nothing."""
    session = await _seed(db_session, cta=CTA_PREFERENCES)
    calls = {"n": 0}

    async def _counted(db, user_id):
        calls["n"] += 1
        return True

    monkeypatch.setattr(chat_router, "has_mf_holdings", _counted)
    await chat_router._history_messages(db_session, USER_ID, session)

    assert calls["n"] == 0
