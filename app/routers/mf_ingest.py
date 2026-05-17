"""FastAPI router — `mf_ingest.py`.

Declares HTTP routes, dependencies (auth, DB session, user context), and maps request/response schemas. Delegates work to ``app.services`` and returns appropriate status codes and Pydantic models.
"""


from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import CurrentUser, get_effective_user
from app.schemas.ingest import (
    MfAaNormalizeOneResponse,
    MfAaNormalizePendingRequest,
    MfAaNormalizePendingResponse,
)
from app.schemas.mf.mfapi import (
    BackfillIsinResultSchema,
    MfapiIngestResultSchema,
    MfapiRefreshRequest,
)
from app.services.mf.mfapi_ingest_service import (
    IngestMode,
    MfapiIngestError,
    backfill_isin_on_existing_rows,
    ingest_mfapi,
)
from app.services.mf_aa_normalizer import (
    get_import_for_user,
    normalize_pending_imports,
    normalize_single_import,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/mf-ingest", tags=["MF Ingest"])


@router.post("/normalize/{import_id}", response_model=MfAaNormalizeOneResponse)
async def normalize_import(
    import_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    aa_import = await get_import_for_user(db, current_user.id, import_id)
    if not aa_import:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="AA import not found")
    out = await normalize_single_import(db, aa_import)
    return MfAaNormalizeOneResponse(
        import_id=out.import_id,
        status=out.status.value,
        inserted=out.inserted,
        skipped_duplicate=out.skipped_duplicate,
        error=out.error,
    )


@router.post("/normalize-pending", response_model=MfAaNormalizePendingResponse)
async def normalize_pending(
    payload: MfAaNormalizePendingRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    outs = await normalize_pending_imports(db, current_user.id, limit=payload.limit)
    rows = [
        MfAaNormalizeOneResponse(
            import_id=o.import_id,
            status=o.status.value,
            inserted=o.inserted,
            skipped_duplicate=o.skipped_duplicate,
            error=o.error,
        )
        for o in outs
    ]
    return MfAaNormalizePendingResponse(
        total_imports=len(rows),
        total_inserted=sum(r.inserted for r in rows),
        total_skipped_duplicate=sum(r.skipped_duplicate for r in rows),
        results=rows,
    )


async def _run_ingest_in_background(
    *,
    mode: IngestMode,
    scheme_codes: list[str] | None,
    dry_run: bool,
    concurrency: int,
) -> None:
    """Run ingest_mfapi on a fresh AsyncSession (the request-scoped one is closed
    by the time a BackgroundTasks callback fires)."""
    from app.database import _get_session_factory

    factory = _get_session_factory()
    async with factory() as bg_db:
        try:
            await ingest_mfapi(
                bg_db,
                mode=mode,
                scheme_codes=scheme_codes,
                dry_run=dry_run,
                concurrency=concurrency,
            )
        except MfapiIngestError as exc:
            logger.error("background mfapi ingest failed: %s", exc)
        except Exception:
            logger.exception("background mfapi ingest crashed")


@router.post("/mfapi/refresh", response_model=MfapiIngestResultSchema)
async def refresh_mfapi(
    payload: MfapiRefreshRequest,
    background: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    """Pull master + NAV from mfapi.in into our DB.

    ``mode=incremental`` runs synchronously and returns the full result —
    suitable for daily-style refreshes (only NAV points newer than the
    per-scheme high-water mark are inserted).

    ``mode=full`` is the one-time historical seed. The full-universe variant is
    too long for an HTTP request and is dispatched as a BackgroundTask; the
    response acknowledges the kickoff. A scoped ``scheme_codes`` full run is
    short enough to execute synchronously.
    """
    is_full_universe = payload.mode is IngestMode.FULL and not payload.scheme_codes
    if is_full_universe:
        background.add_task(
            _run_ingest_in_background,
            mode=payload.mode,
            scheme_codes=None,
            dry_run=payload.dry_run,
            concurrency=payload.concurrency,
        )
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        return MfapiIngestResultSchema(
            mode=payload.mode.value,
            started_at=now,
            finished_at=now,
            dry_run=payload.dry_run,
        )

    try:
        result = await ingest_mfapi(
            db,
            mode=payload.mode,
            scheme_codes=payload.scheme_codes,
            dry_run=payload.dry_run,
            concurrency=payload.concurrency,
        )
    except MfapiIngestError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)
        ) from exc
    return MfapiIngestResultSchema.model_validate(result)


@router.post("/mfapi/backfill-isin", response_model=BackfillIsinResultSchema)
async def backfill_isin(
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    """Fill NULL ISIN / scheme_code on already-ingested AA + NAV rows by joining
    on canonical ``mf_fund_metadata``. Idempotent."""
    result = await backfill_isin_on_existing_rows(db)
    return BackfillIsinResultSchema.model_validate(result)
