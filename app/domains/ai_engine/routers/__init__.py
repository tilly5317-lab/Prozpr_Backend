"""AI modules HTTP router — `__init__.py`.

Exposes ``/api/v1/ai-modules/...`` style endpoints for debugging or direct module invocation. Not always on the live chat path; chat uses ``routers/chat`` + ``ChatBrain`` instead.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.domains.ai_engine.routers.drift_analyzer_router import (
    router as drift_analyzer_router,
)
from app.domains.ai_engine.routers.intent_classifier_router import (
    router as intent_classifier_router,
)
from app.domains.ai_engine.routers.market_commentary_router import (
    router as market_commentary_router,
)
from app.domains.ai_engine.routers.mutual_fund_status_router import (
    router as mutual_fund_status_router,
)
from app.domains.ai_engine.routers.portfolio_query_router import (
    router as portfolio_query_router,
)
from app.domains.ai_engine.routers.rebalancing_router import (
    router as rebalancing_router,
)
from app.domains.ai_engine.routers.risk_profile_router import (
    router as risk_profile_router,
)

router = APIRouter(prefix="/ai-modules")

router.include_router(intent_classifier_router)
router.include_router(market_commentary_router)
router.include_router(portfolio_query_router)
router.include_router(risk_profile_router)
router.include_router(drift_analyzer_router)
router.include_router(mutual_fund_status_router)
router.include_router(rebalancing_router)

__all__ = ["router"]
