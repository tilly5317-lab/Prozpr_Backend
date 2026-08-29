"""FastAPI ASGI entry point — ``uvicorn app.main:app``.

Boots top-to-bottom as a recipe:

    1. configure logging
    2. build the FastAPI instance with the lifespan hook
    2b. attach OpenTelemetry request instrumentation (module scope — see below)
    3. attach CORS middleware
    4. mount every router under ``/api/v1``
    5. register exception handlers

Each step's *how* lives in ``app.core`` — keep this file boring.
"""

from __future__ import annotations

# OpenTelemetry must initialise BEFORE FastAPI/Starlette, the DB drivers, and
# httpx are imported, so its instrumentation attaches. No-op unless
# POSTHOG_API_KEY is set. Keep this as the first executable line.
from app.core.otel import init_otel

init_otel()

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

from app.core.cas_scope import install_cas_scope_listeners
from app.core.config import get_settings
from app.core.exceptions import register_exception_handlers
from app.core.observability import otel_request_hook, otel_response_hook
from app.core.lifespan import lifespan

# Session-wide hooks that scope every read to the user's live CAS statement and
# stamp every new derived row with it. Installed at import so background jobs
# and tests get them too, not just HTTP requests.
install_cas_scope_listeners()

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

# Ship WARNING and above to PostHog Logs. Local stdout/PM2 keeps everything at INFO.
from app.core.otel import attach_otel_logging

attach_otel_logging()


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
# 2b. OpenTelemetry request instrumentation.
#     MUST be at module scope. Calling this inside the lifespan is a SILENT no-op:
#     Starlette caches app.middleware_stack on the first __call__ (which the lifespan
#     scope itself triggers), and the instrumentor only patches build_middleware_stack.
#     app/core/tests/test_otel_instrumentation_slot.py guards this.
#
#     The provider is passed explicitly rather than letting the instrumentor resolve
#     the global one: OTel's global provider can only be set once per process, so
#     after any shutdown/re-init cycle the global points at a dead provider that
#     silently drops spans. See the note in app/core/otel.py.
# ---------------------------------------------------------------------------
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

from app.core.otel import get_tracer_provider

FastAPIInstrumentor.instrument_app(
    app,
    tracer_provider=get_tracer_provider(),
    # The request hook only stamps the start time; the response hook emits the
    # durable http_request event. Both are needed — see observability.py.
    server_request_hook=otel_request_hook,
    client_response_hook=otel_response_hook,
)

# Inside a request these turn "something took 400ms" into "which query, and how
# long did mfapi.in take". Both patch at CLASS level, so they cover clients and
# engines created later — which matters because ``_get_engine()`` is lazy and
# builds the engine on first use, well after this line runs.
#
# In BACKGROUND JOBS these would be ruinous: the mfapi sweep touches ~8k schemes,
# so one run would emit ~25k spans in a single unreadable trace. The schedulers
# wrap their per-item loops in ``suppress_instrumentation()`` and keep only
# run/phase spans — see app/core/job_tracing.py.
SQLAlchemyInstrumentor().instrument(tracer_provider=get_tracer_provider())
HTTPXClientInstrumentor().instrument(tracer_provider=get_tracer_provider())


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
