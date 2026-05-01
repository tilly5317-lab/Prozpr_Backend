"""AI modules HTTP router — `rebalancing.py`.

Exposes ``POST /api/v1/ai-modules/rebalancing/compute`` for direct module
invocation (debug / frontend-driven runs without going through chat).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import CurrentUser, get_ai_user_context, get_effective_user
from app.models.user import User
from app.schemas.ai_modules import (
    RebalancingComputeApiRequest,
    RebalancingComputeApiResponse,
)
from app.services.ai_bridge.rebalancing.service import compute_rebalancing_result


router = APIRouter(prefix="/rebalancing", tags=["AI — Rebalancing"])


@router.post("/compute", response_model=RebalancingComputeApiResponse)
async def compute_rebalancing(
    payload: RebalancingComputeApiRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
    user_ctx: User = Depends(get_ai_user_context),
) -> RebalancingComputeApiResponse:
    outcome = await compute_rebalancing_result(
        user=user_ctx,
        user_question=payload.question,
        db=db,
        acting_user_id=current_user.id,
        chat_session_id=None,
    )
    await db.commit()
    if outcome.blocking_message is not None:
        return RebalancingComputeApiResponse(
            answer_markdown=outcome.blocking_message,
            recommendation_id=None,
            allocation_snapshot_id=None,
            used_cached_allocation=outcome.used_cached_allocation,
            blocking_message=outcome.blocking_message,
        )
    return RebalancingComputeApiResponse(
        answer_markdown=outcome.formatted_text or "",
        recommendation_id=outcome.recommendation_id,
        allocation_snapshot_id=outcome.allocation_snapshot_id,
        used_cached_allocation=outcome.used_cached_allocation,
        blocking_message=None,
    )
