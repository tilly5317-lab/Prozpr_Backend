"""AI modules HTTP router — `portfolio_query.py`.

Exposes ``/api/v1/ai-modules/...`` style endpoints for debugging or direct module invocation. Not always on the live chat path; chat uses ``routers/chat`` + ``ChatBrain`` instead.
"""


from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.dependencies import get_ai_user_context
from app.domains.identity.models.user import User
from app.domains.ai_engine.schemas import PortfolioQueryRequest, PortfolioQueryResponse
from app.domains.ai_engine.services.bridges.portfolio_query_service import generate_portfolio_query_response

router = APIRouter(prefix="/portfolio-query", tags=["AI — Portfolio query"])


@router.post("/answer", response_model=PortfolioQueryResponse)
async def portfolio_answer(
    payload: PortfolioQueryRequest,
    user_ctx: User = Depends(get_ai_user_context),
):
    text = await generate_portfolio_query_response(user_ctx, payload.question)
    return PortfolioQueryResponse(answer_markdown=text)
