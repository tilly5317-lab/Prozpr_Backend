"""AI modules HTTP router — `market_commentary.py`.

Exposes ``/api/v1/ai-modules/...`` style endpoints for debugging or direct module invocation. Not always on the live chat path; chat uses ``routers/chat`` + ``ChatBrain`` instead.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.dependencies import CurrentUser, get_effective_user
from app.domains.ai_engine.schemas import MarketCommentaryResponse
from app.domains.market_commentary.services.market_commentary_engine import (
    generate_market_commentary,
)

router = APIRouter(prefix="/market-commentary", tags=["AI — Market commentary"])


@router.post("/generate", response_model=MarketCommentaryResponse)
async def generate_commentary(
    _current_user: CurrentUser = Depends(get_effective_user),
):
    text = await generate_market_commentary(user_question="", conversation_history=None)
    return MarketCommentaryResponse(document_markdown=text)
