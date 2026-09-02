"""FastAPI router — `__init__.py`.

Declares HTTP routes, dependencies (auth, DB session, user context), and maps request/response schemas. Delegates work to ``app.services`` and returns appropriate status codes and Pydantic models.
"""

from app.routers.health import router as health_router
from app.domains.identity.routers.auth_router import router as auth_router
from app.domains.identity.routers.onboarding_router import router as onboarding_router
from app.domains.profile.routers.profile_router import router as profile_router
from app.domains.goals.routers.goals_router import router as goals_router
from app.domains.portfolio.routers.portfolio_router import router as portfolio_router
from app.domains.chat.routers.chat_router import router as chat_router
from app.domains.advisory.routers.meeting_notes_router import (
    router as meeting_notes_router,
)
from app.domains.notifications.routers.notifications_router import (
    router as notifications_router,
)
from app.domains.advisory.routers.discovery_router import router as discovery_router
from app.domains.rebalancing.routers.rebalancing_router import (
    router as rebalancing_router,
)
from app.domains.additional_investment.routers.additional_investment_router import (
    router as additional_investment_router,
)
from app.domains.advisory.routers.ips_router import router as ips_router
from app.domains.advisory.routers.team_call_router import router as team_call_router
from app.domains.identity.routers.linked_accounts_router import (
    router as linked_accounts_router,
)
from app.domains.identity.routers.family_router import router as family_router
from app.domains.ingestion.routers.simbanks_router import router as simbanks_router
from app.domains.mutual_funds.routers import router as mf_data_router
from app.domains.ingestion.routers.cas_uploads_router import (
    router as cas_uploads_router,
)
from app.domains.ingestion.routers.mf_ingest_router import router as mf_ingest_router
from app.domains.cashflow.routers.cashflow_router import router as cashflow_router
from app.domains.ai_engine.routers import router as ai_modules_router
from app.domains.support.routers.support_router import router as support_router
from app.domains.benchmarks.routers import router as benchmarks_router
from app.domains.execution.routers.fp_router import router as fp_router
from app.domains.privacy.routers.privacy_router import router as privacy_router
from app.domains.vr_data.routers import router as vr_router

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
    additional_investment_router,
    ips_router,
    team_call_router,
    linked_accounts_router,
    family_router,
    simbanks_router,
    mf_data_router,
    mf_ingest_router,
    cas_uploads_router,
    cashflow_router,
    ai_modules_router,
    support_router,
    benchmarks_router,
    fp_router,
    privacy_router,
    vr_router,
]
