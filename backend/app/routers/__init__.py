"""FastAPI router — `__init__.py`.

Declares HTTP routes, dependencies (auth, DB session, user context), and maps request/response schemas. Delegates work to ``app.services`` and returns appropriate status codes and Pydantic models.
"""


from app.routers.health import router as health_router
from app.routers.auth import router as auth_router
from app.routers.onboarding import router as onboarding_router
from app.routers.profile import router as profile_router
from app.routers.goals import router as goals_router
from app.routers.portfolio import router as portfolio_router
from app.routers.chat import router as chat_router
from app.routers.meeting_notes import router as meeting_notes_router
from app.routers.notifications import router as notifications_router
from app.routers.discovery import router as discovery_router
from app.routers.rebalancing import router as rebalancing_router
from app.routers.ips import router as ips_router
from app.routers.linked_accounts import router as linked_accounts_router
from app.routers.family import router as family_router
from app.routers.simbanks import router as simbanks_router
from app.routers.mf_ingest import router as mf_ingest_router
from app.routers.ai_modules import router as ai_modules_router

all_routers = [
    health_router,
    auth_router,
    onboarding_router,
    profile_router,
    goals_router,
    portfolio_router,
    chat_router,
    meeting_notes_router,
    notifications_router,
    discovery_router,
    rebalancing_router,
    ips_router,
    linked_accounts_router,
    family_router,
    simbanks_router,
    mf_ingest_router,
    ai_modules_router,
]
