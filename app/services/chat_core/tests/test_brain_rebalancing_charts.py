"""Brain integration — rebalancing branch produces chart_payloads via central selector."""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.models  # noqa: F401
from app.database import Base
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
async def fixture_user(db_session):
    suffix = uuid.uuid4().hex[:8]
    user = User(
        id=uuid.uuid4(),
        email=f"rebal_brain_{suffix}@example.com",
        country_code="+91",
        mobile="9999999999",
        phone=f"+91-9999{suffix}",
        date_of_birth=date(1990, 1, 1),
    )
    db_session.add(user)
    await db_session.flush()
    return user


def _classification(intent: str = "rebalancing"):
    return type("C", (), {
        "intent": type("I", (), {"value": intent})(),
        "confidence": 0.95,
        "reasoning": "test",
        "out_of_scope_message": None,
    })()


@pytest.mark.asyncio
async def test_rebal_turn_attaches_chart_payloads(db_session, fixture_user):
    """When the selector returns names AND the engine response has trades, payloads ship."""
    # Build a richer response with one buy
    action = MagicMock()
    action.recommended_fund = "Fund A"
    action.fund_name = "Fund A"
    action.sub_category = "Large Cap Fund"
    action.pass1_buy_amount = Decimal("100000")
    action.pass1_sell_amount = None
    action.pass2_sell_amount = None
    action.present_allocation_inr = Decimal("500000")
    subgroup = MagicMock()
    subgroup.asset_subgroup = "low_beta_equities"
    subgroup.goal_target_inr = Decimal("600000")
    subgroup.actions = [action]
    response = MagicMock()
    response.subgroups = [subgroup]
    response.totals = MagicMock(total_tax_estimate_inr=Decimal(0), total_exit_load_inr=Decimal(0))

    dispatch_result = type("R", (), {
        "text": "Here's your rebalance plan.",
        "snapshot_id": None,
        "rebalancing_recommendation_id": uuid.uuid4(),
        "rebalancing_response": response,
    })()

    turn = ChatTurnInput(
        db=db_session,
        user_id=fixture_user.id,
        session_id=uuid.uuid4(),
        user_question="rebalance my portfolio",
        conversation_history=[],
        client_context=None,
        user_ctx=fixture_user,
    )

    with patch(
        "app.services.chat_core.brain.classify_user_message",
        new=AsyncMock(return_value=_classification("rebalancing")),
    ), patch(
        "app.services.chat_core.brain.select_charts",
        new=AsyncMock(return_value=["category_gap_bar", "buy_sell_ledger"]),
    ), patch(
        "app.services.ai_bridge.chat_dispatcher.dispatch_chat",
        new=AsyncMock(return_value=dispatch_result),
    ):
        result = await ChatBrain().run_turn(turn)

    assert result.intent == "rebalancing"
    assert result.chart_payloads is not None
    types_returned = {p["type"] for p in result.chart_payloads}
    assert "category_gap_bar" in types_returned
    assert "buy_sell_ledger" in types_returned
