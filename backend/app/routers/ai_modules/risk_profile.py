"""AI modules HTTP router — `risk_profile.py`.

Exposes ``/api/v1/ai-modules/...`` style endpoints for debugging or direct module invocation. Not always on the live chat path; chat uses ``routers/chat`` + ``ChatBrain`` instead.
"""


from __future__ import annotations

from fastapi import APIRouter, Depends

from app.dependencies import CurrentUser, get_effective_user
from app.schemas.ai_modules import AIModuleStatusResponse

router = APIRouter(prefix="/risk-profile", tags=["AI — Risk profile"])


@router.get("/status", response_model=AIModuleStatusResponse)
async def risk_profile_module_status(
    _current_user: CurrentUser = Depends(get_effective_user),
):
    return AIModuleStatusResponse(
        module="risk_profile",
        status="planned",
        detail=(
            "Risk scoring and narrative use AI_Agents/src/risk_profiling (LangChain). "
            "Set RISK_PROFILING_API_KEY (or ANTHROPIC_API_KEY) for Claude; "
            "persisted risk level is exposed via /profile. Chat spine reads saved risk from the DB."
        ),
    )
