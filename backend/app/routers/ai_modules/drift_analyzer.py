"""AI modules HTTP router — `drift_analyzer.py`.

Exposes ``/api/v1/ai-modules/...`` style endpoints for debugging or direct module invocation. Not always on the live chat path; chat uses ``routers/chat`` + ``ChatBrain`` instead.
"""


from __future__ import annotations

from fastapi import APIRouter, Depends

from app.dependencies import CurrentUser, get_effective_user
from app.schemas.ai_modules import AIModuleStatusResponse

router = APIRouter(prefix="/drift-analyzer", tags=["AI — Drift analyzer"])


@router.get("/status", response_model=AIModuleStatusResponse)
async def drift_module_status(
    _current_user: CurrentUser = Depends(get_effective_user),
):
    return AIModuleStatusResponse(
        module="drift_analyzer",
        status="stub",
        detail="Portfolio drift vs target allocation — not wired to HTTP yet.",
    )
