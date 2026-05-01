"""AI modules HTTP router — `asset_allocation.py`.

Exposes ``/api/v1/ai-modules/...`` style endpoints for debugging or direct module invocation. Not always on the live chat path; chat uses ``routers/chat`` + ``ChatBrain`` instead.
"""


from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import CurrentUser, get_ai_user_context, get_effective_user
from app.models.user import User
from app.schemas.ai_modules import AssetAllocationRequest, AssetAllocationResponse
from app.services.ai_bridge.asset_allocation import generate_asset_allocation_response

router = APIRouter(prefix="/asset-allocation", tags=["AI — Asset allocation"])


@router.post("/recommend", response_model=AssetAllocationResponse)
async def recommend_allocation(
    payload: AssetAllocationRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
    user_ctx: User = Depends(get_ai_user_context),
):
    text = await generate_asset_allocation_response(
        user_ctx,
        payload.question,
        db=db,
        persist_recommendation=True,
        acting_user_id=current_user.id,
    )
    await db.commit()
    return AssetAllocationResponse(answer_markdown=text)
