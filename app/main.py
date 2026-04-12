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

from app.config import get_settings
from app.database import create_all_tables, dispose_engine
from app.routers import all_routers

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
        await create_all_tables()
        logger.info("Database tables ready")
    except Exception as e:
        logger.error("Database setup error: %s", e)

    logger.info("Server ready! Docs at /docs")
    logger.info("=" * 60)

    yield

    logger.info("Shutting down Ask Tilly API...")
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
)

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
