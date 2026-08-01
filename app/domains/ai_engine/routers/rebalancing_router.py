"""AI modules HTTP router — `rebalancing.py`.

Exposes ``POST /api/v1/ai-modules/rebalancing/compute`` for direct module
invocation (debug / frontend-driven runs without going through chat).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import CurrentUser, get_ai_user_context, get_effective_user
from app.core.progress import clear_progress, get_progress, set_progress
from app.domains.identity.models.user import User
from app.domains.ai_engine.schemas import (
    RebalancingComputeApiRequest,
    RebalancingComputeApiResponse,
)
from app.domains.rebalancing.services.rebal_engine.service import (
    compute_rebalancing_result,
)


router = APIRouter(prefix="/rebalancing", tags=["AI — Rebalancing"])

_PROGRESS_TASK = "rebalance_compute"


class ComputeProgressResponse(BaseModel):
    """Live stage of an in-flight compute (polled while the POST runs).

    ``messages`` is the full stage history so far (oldest first) so the UI can
    show every completed step even when stages advance faster than the poll.
    """

    active: bool
    progress_pct: float
    message: str | None = None
    messages: list[str] = []


@router.post("/compute", response_model=RebalancingComputeApiResponse)
async def compute_rebalancing(
    payload: RebalancingComputeApiRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
    user_ctx: User = Depends(get_ai_user_context),
) -> RebalancingComputeApiResponse:
    # Publish each pipeline stage to the in-process progress store so the
    # Invest page's poller (GET /compute-progress) can show real stage + %.
    async def _progress(pct: float, message: str) -> None:
        set_progress(current_user.id, _PROGRESS_TASK, pct, message)

    try:
        outcome = await compute_rebalancing_result(
            user=user_ctx,
            user_question=payload.question,
            db=db,
            acting_user_id=current_user.id,
            chat_session_id=None,
            progress=_progress,
        )
        await db.commit()
    finally:
        clear_progress(current_user.id, _PROGRESS_TASK)
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
        allocation_snapshot_id=None,
        used_cached_allocation=outcome.used_cached_allocation,
        blocking_message=None,
    )


@router.get("/compute-progress", response_model=ComputeProgressResponse)
async def compute_progress(
    current_user: CurrentUser = Depends(get_effective_user),
) -> ComputeProgressResponse:
    """Live progress of this user's in-flight rebalancing compute (if any)."""
    return ComputeProgressResponse(**get_progress(current_user.id, _PROGRESS_TASK))
