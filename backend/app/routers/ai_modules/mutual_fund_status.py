"""AI modules HTTP router — `mutual_fund_status.py`.

Exposes ``/api/v1/ai-modules/...`` style endpoints for debugging or direct module invocation. Not always on the live chat path; chat uses ``routers/chat`` + ``ChatBrain`` instead.
"""


from __future__ import annotations

from fastapi import APIRouter, Depends

from app.dependencies import CurrentUser, get_effective_user
from app.schemas.ai_modules import AIModuleStatusResponse

router = APIRouter(prefix="/mutual-fund-status", tags=["AI — Mutual fund status"])


@router.get("/status", response_model=AIModuleStatusResponse)
async def mf_status_module_status(
    _current_user: CurrentUser = Depends(get_effective_user),
):
    return AIModuleStatusResponse(
        module="mutual_fund_status",
        status="stub",
        detail="MF scheme status narrative — not wired to HTTP yet.",
    )
