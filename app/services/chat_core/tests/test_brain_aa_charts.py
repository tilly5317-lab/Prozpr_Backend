"""Brain integration — AA branch produces chart_payloads."""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.models  # noqa: F401  -- registers all ORM tables
from app.database import Base
from app.models.portfolio import Portfolio, PortfolioAllocation
from app.models.user import User
from app.services.chat_core.brain import ChatBrain
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
async def fixture_user_with_portfolio(db_session):
    suffix = uuid.uuid4().hex[:8]
    user = User(
        id=uuid.uuid4(),
        email=f"brain_test_{suffix}@example.com",
        country_code="+91",
        mobile="9999999999",
        phone=f"+91-9999{suffix}",
        date_of_birth=date(1990, 1, 1),
    )
    db_session.add(user)
    await db_session.flush()
    portfolio = Portfolio(id=uuid.uuid4(), user_id=user.id, is_primary=True)
    db_session.add(portfolio)
    await db_session.flush()
    for cls, amount, pct in (
        ("Equity", Decimal("700000"), Decimal("70.00")),
        ("Debt", Decimal("250000"), Decimal("25.00")),
        ("Cash", Decimal("50000"), Decimal("5.00")),
    ):
        db_session.add(PortfolioAllocation(
            id=uuid.uuid4(),
            portfolio_id=portfolio.id,
            asset_class=cls,
            amount=amount,
            allocation_percentage=pct,
        ))
    await db_session.flush()
    return user


def _fake_classification(intent_value: str = "asset_allocation"):
    return type(
        "C",
        (),
        {
            "intent": type("I", (), {"value": intent_value})(),
            "confidence": 0.99,
            "reasoning": "test",
            "out_of_scope_message": None,
        },
    )()


def _fake_dispatch_result():
    return type(
        "R",
        (),
        {
            "text": "Your portfolio is 70% equity.",
            "snapshot_id": None,
            "rebalancing_recommendation_id": None,
        },
    )()


@pytest.mark.asyncio
async def test_aa_turn_attaches_chart_payloads(db_session, fixture_user_with_portfolio):
    user = fixture_user_with_portfolio
    turn = ChatTurnInput(
        db=db_session,
        user_id=user.id,
        session_id=uuid.uuid4(),
        user_question="show me my asset mix",
        conversation_history=[],
        client_context=None,
        user_ctx=user,
    )

    with patch(
        "app.services.chat_core.brain.classify_user_message",
        new=AsyncMock(return_value=_fake_classification("asset_allocation")),
    ), patch(
        "app.services.chat_core.brain.select_charts",
        new=AsyncMock(return_value=["current_donut"]),
    ), patch(
        "app.services.ai_bridge.chat_dispatcher.dispatch_chat",
        new=AsyncMock(return_value=_fake_dispatch_result()),
    ):
        result = await ChatBrain().run_turn(turn)

    assert result.intent == "asset_allocation"
    assert result.chart_payloads is not None
    assert len(result.chart_payloads) == 1
    assert result.chart_payloads[0]["type"] == "current_donut"


@pytest.mark.asyncio
async def test_aa_turn_charts_empty_on_selector_failure(
    db_session, fixture_user_with_portfolio,
):
    """Selector returns []. The reply still ships, just without charts."""
    user = fixture_user_with_portfolio
    turn = ChatTurnInput(
        db=db_session,
        user_id=user.id,
        session_id=uuid.uuid4(),
        user_question="hi",
        conversation_history=[],
        client_context=None,
        user_ctx=user,
    )

    with patch(
        "app.services.chat_core.brain.classify_user_message",
        new=AsyncMock(return_value=_fake_classification("asset_allocation")),
    ), patch(
        "app.services.chat_core.brain.select_charts",
        new=AsyncMock(return_value=[]),
    ), patch(
        "app.services.ai_bridge.chat_dispatcher.dispatch_chat",
        new=AsyncMock(return_value=_fake_dispatch_result()),
    ):
        result = await ChatBrain().run_turn(turn)

    assert result.chart_payloads is None
