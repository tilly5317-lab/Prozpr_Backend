"""AI modules HTTP router — `__init__.py`.

Exposes ``/api/v1/ai-modules/...`` style endpoints for debugging or direct module invocation. Not always on the live chat path; chat uses ``routers/chat`` + ``ChatBrain`` instead.
"""


from __future__ import annotations

from fastapi import APIRouter

from app.routers.ai_modules.asset_allocation import router as asset_allocation_router
from app.routers.ai_modules.drift_analyzer import router as drift_analyzer_router
from app.routers.ai_modules.intent_classifier import router as intent_classifier_router
from app.routers.ai_modules.market_commentary import router as market_commentary_router
from app.routers.ai_modules.mutual_fund_status import router as mutual_fund_status_router
from app.routers.ai_modules.portfolio_query import router as portfolio_query_router
from app.routers.ai_modules.risk_profile import router as risk_profile_router

router = APIRouter(prefix="/ai-modules")

router.include_router(intent_classifier_router)
router.include_router(market_commentary_router)
router.include_router(portfolio_query_router)
router.include_router(risk_profile_router)
router.include_router(asset_allocation_router)
router.include_router(drift_analyzer_router)
router.include_router(mutual_fund_status_router)

__all__ = ["router"]
