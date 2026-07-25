"""FastAPI ASGI entry point — ``uvicorn app.main:app``.

Boots top-to-bottom as a recipe:

    1. configure logging
    2. build the FastAPI instance with the lifespan hook
    3. attach CORS middleware
    4. mount every router under ``/api/v1``
    5. register exception handlers

Each step's *how* lives in ``app.core`` — keep this file boring.
"""

from __future__ import annotations

# New Relic must initialise BEFORE FastAPI/Starlette, the DB drivers, and httpx
# are imported, so its import hooks can instrument them. No-op unless
# NEW_RELIC_LICENSE_KEY is set. Keep this as the first executable line.
from app.core.observability import init_newrelic

init_newrelic()

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Side-effect import: registers every domain's ORM class with ``Base.metadata``
# before any request can fire a query. Some domains (today: equities) have ORM
# models but no router/service that pulls them into the runtime import graph,
# so the User mapper's ``relationship("StockTransaction", ...)`` fails to
# resolve on the first ``select(User)`` unless this file is loaded.
# Keep at the top of the import block so registration happens once at boot.
import app.all_models  # noqa: F401

from app.core.config import get_settings
from app.core.exceptions import register_exception_handlers
from app.core.lifespan import lifespan
from app.routers import all_routers
from app.routers.tags import OPENAPI_TAG_METADATA


# ---------------------------------------------------------------------------
# 1. Logging — root at INFO; muzzle a handful of chatty third-party libraries.
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
for _noisy in ("httpx", "primp", "ddgs", "duckduckgo_search"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)


# ---------------------------------------------------------------------------
# 2. FastAPI instance.
# ---------------------------------------------------------------------------
settings = get_settings()

app = FastAPI(
    title=settings.PROJECT_NAME,
    description="Ask PI — AI-powered wealth management platform API",
    version=settings.VERSION,
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
    openapi_tags=OPENAPI_TAG_METADATA,
)


# ---------------------------------------------------------------------------
# 3. CORS. ``CORS_ALLOW_ANY_ORIGIN`` is dev-only — production sets an explicit
#    ``ALLOWED_ORIGINS`` list. Credentials are always allowed; we trust the
#    upstream allow-list / regex to keep this safe.
# ---------------------------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=[] if settings.CORS_ALLOW_ANY_ORIGIN else settings.ALLOWED_ORIGINS,
    allow_origin_regex=".*" if settings.CORS_ALLOW_ANY_ORIGIN else None,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    # Chrome sends a Private-Network-Access preflight when a public https site
    # (e.g. the GitHub Pages FP sandbox tester) calls a loopback/private address;
    # without this the preflight is refused even for allow-listed origins.
    allow_private_network=True,
)


# ---------------------------------------------------------------------------
# 4. Routers — order is set by ``app.routers.__init__.all_routers``.
# ---------------------------------------------------------------------------
for router in all_routers:
    app.include_router(router, prefix=settings.API_V1_PREFIX)

# ---------------------------------------------------------------------------
# 5. Exception handlers — translate well-known errors into stable JSON.
# ---------------------------------------------------------------------------
register_exception_handlers(app)
