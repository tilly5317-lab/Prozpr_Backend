"""FastAPI router — `mf_ingest.py`.

Declares HTTP routes, dependencies (auth, DB session, user context), and maps request/response schemas. Delegates work to ``app.services`` and returns appropriate status codes and Pydantic models.
"""


from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import CurrentUser, get_effective_user
from app.schemas.ingest import (
    CamsPdfImportResponse,
    MfAaNormalizeOneResponse,
    MfAaNormalizePendingRequest,
    MfAaNormalizePendingResponse,
)
from app.schemas.mf.mfapi import (
    BackfillIsinResultSchema,
    MfapiIngestResultSchema,
    MfapiRefreshRequest,
)
from app.services.cams_cas_ingest import CamsPdfParseError, ingest_cams_pdf
from app.services.effective_risk_profile import maybe_recalculate_effective_risk
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

# Generous ceiling — a multi-year consolidated CAS rarely exceeds a few MB.
_MAX_CAS_PDF_BYTES = 20 * 1024 * 1024


@router.post("/cams-pdf", response_model=CamsPdfImportResponse)
async def ingest_cams_statement_pdf(
    file: UploadFile = File(..., description="CAMS / KFintech Consolidated Account Statement PDF"),
    password: str = Form(
        ...,
        description="Password set when generating the CAS (commonly your PAN in capitals).",
    ),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    """Upload a CAMS / KFintech Consolidated Account Statement (CAS) PDF.

    Parses the statement, stores the raw rows in ``mf_aa_imports`` /
    ``mf_aa_summaries`` / ``mf_aa_transactions``, normalizes them into
    ``mf_transactions``, and refreshes the primary-portfolio bucket allocation
    (Cash / Debt / Equity / Other).

    This is the replacement for the (paused) Finvu account-aggregator
    fetch-by-mobile flow — see ``POST /portfolio/finvu/sync`` (deprecated).
    """
    filename = (file.filename or "").strip()
    if not filename.lower().endswith(".pdf") and file.content_type not in (
        None,
        "application/pdf",
        "application/octet-stream",
    ):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Please upload the Consolidated Account Statement as a PDF file.",
        )

    data = await file.read()
    if not data:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="The uploaded file is empty.")
    if len(data) > _MAX_CAS_PDF_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="The PDF is too large (limit 20 MB).",
        )
    if not (password or "").strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="A PDF password is required.")

    try:
        result = await ingest_cams_pdf(
            db,
            current_user.id,
            file_bytes=data,
            password=password,
            source_filename=filename or None,
        )
    except CamsPdfParseError as exc:
        raise HTTPException(
            status_code=(
                status.HTTP_400_BAD_REQUEST if exc.bad_password else status.HTTP_422_UNPROCESSABLE_ENTITY
            ),
            detail=str(exc),
        ) from exc

    await maybe_recalculate_effective_risk(db, current_user.id, "cams_pdf_ingest")
    await db.commit()

    if result.status == "FAILED":
        message = (
            f"Parsed {result.schemes} scheme(s) across {result.folios} folio(s), but normalization "
            f"into transactions failed: {result.normalize_error or 'unknown error'}. "
            "Retry via POST /mf-ingest/normalize/{import_id}."
        )
    else:
        message = (
            f"Imported {result.schemes} scheme(s) across {result.folios} folio(s); "
            f"{result.mf_transactions_inserted} transaction(s) added "
            f"({result.mf_transactions_skipped_duplicate} duplicate(s) skipped). "
            f"Portfolio value updated to INR {result.total_value_inr:,.2f}."
        )
        if result.profile_fields_filled:
            message += (
                " Filled your profile from the statement: "
                + ", ".join(result.profile_fields_filled).replace("_", " ")
                + "."
            )

    return CamsPdfImportResponse(
        import_id=result.import_id,
        status=result.status,
        cas_file_type=result.cas_file_type,
        cas_type=result.cas_type,
        statement_period_from=result.statement_period_from,
        statement_period_to=result.statement_period_to,
        folios=result.folios,
        schemes=result.schemes,
        aa_transactions_parsed=result.aa_transactions_parsed,
        mf_transactions_inserted=result.mf_transactions_inserted,
        mf_transactions_skipped_duplicate=result.mf_transactions_skipped_duplicate,
        portfolio_allocation_rows=result.portfolio_allocation_rows,
        total_value_inr=result.total_value_inr,
        normalize_error=result.normalize_error,
        profile_fields_filled=result.profile_fields_filled,
        message=message,
    )


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
