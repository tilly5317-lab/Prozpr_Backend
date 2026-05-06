"""FastAPI application factory and ASGI entry (`uvicorn app.main:app`).

Builds the ``FastAPI`` instance, attaches CORS, registers ``app.routers.all_routers``,
and runs startup/shutdown hooks (e.g. optional table creation, engine disposal).
Global exception handlers translate ``ValidationError`` and unexpected errors into JSON.

"""


from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from sqlalchemy.engine import make_url

from app.config import get_settings
from app.database import apply_postgres_schema_patches, create_all_tables, dispose_engine
from app.routers import all_routers
from app.routers.tags import OPENAPI_TAG_METADATA
from app.services.mf.mfapi_scheduler import shutdown_scheduler, start_scheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

for _noisy in ("httpx", "primp", "ddgs", "duckduckgo_search"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(application: FastAPI):
    logger.info("=" * 60)
    logger.info("Starting Ask Tilly API v2.0")
    logger.info("=" * 60)

    try:
        db_url = get_settings().get_database_url()
        parsed = make_url(db_url)
        if parsed.drivername.startswith("postgresql"):
            logger.info(
                "Database engine: postgresql (host=%s, db=%s). Run `alembic upgrade head` on RDS for migrations.",
                parsed.host or "?",
                parsed.database or "?",
            )
        elif parsed.drivername.startswith("sqlite"):
            logger.warning("Database engine: sqlite (ALLOW_SQLITE dev mode only)")
        if get_settings().skip_startup_db_ddl():
            logger.info(
                "Skipping startup DB DDL (SKIP_STARTUP_DB_DDL=true). "
                "Ensure schema exists (e.g. alembic upgrade head on RDS)."
            )
        else:
            await create_all_tables()
            try:
                await apply_postgres_schema_patches()
            except Exception as patch_exc:
                logger.warning(
                    "Postgres schema patches failed (check DB permissions / table chat_ai_module_runs): %s",
                    patch_exc,
                )
            logger.info("Database tables ready (create_all).")
    except Exception as e:
        logger.error("Database setup error: %s", e)

    if get_settings().mfapi_scheduler_enabled():
        try:
            start_scheduler()
        except Exception as sched_exc:
            logger.warning("mfapi scheduler failed to start: %s", sched_exc)
    else:
        logger.info("mfapi scheduler disabled (MFAPI_SCHEDULER_ENABLED is false)")

    logger.info("Server ready! Docs at /docs")
    logger.info("=" * 60)

    yield

    logger.info("Shutting down Ask Tilly API...")
    await shutdown_scheduler()
    await dispose_engine()
    logger.info("Shutdown complete")


settings = get_settings()

app = FastAPI(
    title=settings.PROJECT_NAME,
    description="Ask Tilly - AI-powered wealth management platform API",
    version=settings.VERSION,
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
    openapi_tags=OPENAPI_TAG_METADATA,
)

if settings.CORS_ALLOW_ANY_ORIGIN:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[],
        allow_origin_regex=".*",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
else:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.ALLOWED_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

for router in all_routers:
    app.include_router(router, prefix=settings.API_V1_PREFIX)


# Exception handlers
@app.exception_handler(ValidationError)
async def validation_exception_handler(request, exc):
    logger.error("Validation error: %s", exc)
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": str(exc)},
    )


def _is_db_error(exc: Exception, keywords: list[str]) -> bool:
    msg = str(exc).lower()
    if any(kw in msg for kw in keywords):
        return True
    cause = getattr(exc, "__cause__", None)
    return _is_db_error(cause, keywords) if cause else False


@app.exception_handler(Exception)
async def general_exception_handler(request, exc):
    if _is_db_error(exc, ["password authentication failed", "invalidpassworderror"]):
        logger.error("Database auth failed: %s", exc)
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"detail": "Database connection failed: authentication error. Check DATABASE_URL."},
        )
    if _is_db_error(exc, ["getaddrinfo failed", "name or service not known"]):
        logger.error("Database host unreachable: %s", exc)
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"detail": "Database connection failed: host unreachable. Check DATABASE_URL."},
        )
    if _is_db_error(exc, ["connection was closed"]):
        logger.error("Database connection closed: %s", exc)
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"detail": "Database connection was closed. Retry the request."},
        )
    logger.error("Unexpected error: %s", exc, exc_info=True)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "An unexpected error occurred"},
    )
