"""AI modules HTTP router — `portfolio_query.py`.

Exposes ``/api/v1/ai-modules/...`` style endpoints for debugging or direct module invocation. Not always on the live chat path; chat uses ``routers/chat`` + ``ChatBrain`` instead.
"""


from __future__ import annotations

from fastapi import APIRouter, Depends

from app.dependencies import get_ai_user_context
from app.models.user import User
from app.schemas.ai_modules import PortfolioQueryRequest, PortfolioQueryResponse
from app.services.ai_bridge.portfolio_query_service import generate_portfolio_query_response

router = APIRouter(prefix="/portfolio-query", tags=["AI — Portfolio query"])


@router.post("/answer", response_model=PortfolioQueryResponse)
async def portfolio_answer(
    payload: PortfolioQueryRequest,
    user_ctx: User = Depends(get_ai_user_context),
):
    text = await generate_portfolio_query_response(user_ctx, payload.question)
    return PortfolioQueryResponse(answer_markdown=text)
