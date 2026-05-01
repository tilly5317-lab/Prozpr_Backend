"""Persist a rebalancing engine response as a REBALANCING_TRADES row."""

from __future__ import annotations

import uuid
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.rebalancing import (
    RebalancingRecommendation,
    RebalancingStatus,
    RecommendationType,
)
from app.services.ai_bridge.common import ensure_ai_agents_path
from app.services.portfolio_service import get_or_create_primary_portfolio

ensure_ai_agents_path()

from Rebalancing.models import RebalancingComputeResponse  # type: ignore[import-not-found]  # noqa: E402


async def persist_rebalancing_recommendation(
    db: AsyncSession,
    user_id: uuid.UUID,
    response: RebalancingComputeResponse,
    *,
    chat_session_id: Optional[uuid.UUID],
    source_allocation_id: Optional[uuid.UUID],
    used_cached_allocation: bool,
    user_question: Optional[str] = None,
) -> uuid.UUID:
    """Write the engine response and return the new recommendation row id."""
    portfolio = await get_or_create_primary_portfolio(db, user_id)
    payload: dict[str, Any] = {
        "source": "rebalancing_engine",
        "rebalancing_response": response.model_dump(mode="json"),
        "request_id": str(response.metadata.request_id),
        "used_cached_allocation": used_cached_allocation,
        "chat_session_id": str(chat_session_id) if chat_session_id else None,
        "user_question": user_question,
    }
    rec = RebalancingRecommendation(
        portfolio_id=portfolio.id,
        recommendation_type=RecommendationType.REBALANCING_TRADES,
        source_allocation_id=source_allocation_id,
        status=RebalancingStatus.pending,
        recommendation_data=payload,
        reason="Rebalancing trade plan (engine output)",
    )
    db.add(rec)
    await db.flush()
    return rec.id
