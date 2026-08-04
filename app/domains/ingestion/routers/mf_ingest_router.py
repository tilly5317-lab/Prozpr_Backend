"""FastAPI router — `mf_ingest.py`.

Declares HTTP routes, dependencies (auth, DB session, user context), and maps request/response schemas. Delegates work to ``app.services`` and returns appropriate status codes and Pydantic models.
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import date

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
    status,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.database import get_db
from app.core.dependencies import CurrentUser, get_effective_user
from app.domains.ingestion.schemas import (
    CamsCapabilitiesResponse,
    CamsPdfImportResponse,
    CamsStatementRequestBody,
    CamsStatementRequestResponse,
    CasDocumentDownloadResponse,
    CasDocumentItem,
    CasDocumentListResponse,
    MfAaNormalizeOneResponse,
    MfAaNormalizePendingRequest,
    MfAaNormalizePendingResponse,
)
from app.domains.ingestion.services.cams_pdf_stage import (
    delete_cas_object,
    presign_cas_download,
    stage_enabled,
)
from app.domains.mutual_funds.models import UserCasDocument
from app.domains.ingestion.services.casparser_client import (
    CasParserApiError,
    get_casparser_client,
)
from app.domains.mutual_funds.schemas.mfapi import (
    BackfillIsinResultSchema,
    MfapiIngestResultSchema,
    MfapiRefreshRequest,
)
from app.domains.ingestion.services.cams_cas_ingest import (
    CamsPdfParseError,
    ingest_cams_pdf,
)
from app.domains.portfolio.services.networth_history_service import (
    create_job,
    has_running_job,
    run_networth_backfill,
)
from app.domains.profile.services._effective_risk import (
    maybe_recalculate_effective_risk,
)
from app.domains.mutual_funds.services.mfapi_ingest_service import (
    IngestMode,
    MfapiIngestError,
    backfill_isin_on_existing_rows,
    ingest_mfapi,
)
from app.domains.ingestion.services.mf_aa_normalizer import (
    get_import_for_user,
    normalize_pending_imports,
    normalize_single_import,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/mf-ingest", tags=["MF Ingest"])

# Generous ceiling — a multi-year consolidated CAS rarely exceeds a few MB.
_MAX_CAS_PDF_BYTES = 20 * 1024 * 1024

# Full-history statement window for the CAS mailback request. CAMS accepts a
# very early from_date and simply returns everything the investor has.
_CAS_REQUEST_FROM_DATE = "1990-01-01"

# Per-user cooldown so a double-tap can't spam the registrar (each request
# consumes an API credit and lands another email in the user's inbox).
# In-process only — good enough for a single-instance deployment.
_CAS_REQUEST_COOLDOWN_SECONDS = 60.0
_cas_request_last_sent: dict[uuid.UUID, float] = {}


# CAS Parser feature names gating each half of the flow (from /v1/credits
# enabled_features). Cached briefly so the flow's mount-time capability check
# doesn't hit the API on every modal open.
_FEATURE_PARSER = "cams_kfintech_cas_parser"
_FEATURE_GENERATOR = "kfintech_cas_generator"
_CAPS_CACHE_TTL_SECONDS = 900.0
_caps_cache: dict[str, object] = {}


@router.get("/cams-capabilities", response_model=CamsCapabilitiesResponse)
async def cams_capabilities(
    current_user: CurrentUser = Depends(get_effective_user),
):
    """Which halves of the CAMS import the configured API key supports.

    Lets the frontend start the flow at the upload step (with manual-generation
    guidance) on plans without the CAS generator, and light the email step up
    automatically after a plan upgrade — no deploy needed.
    """
    if not Settings.casparser_enabled():
        return CamsCapabilitiesResponse(parse_available=False, mailback_available=False)

    now = time.monotonic()
    cached = _caps_cache.get("data")
    ts = _caps_cache.get("ts")
    if (
        cached is not None
        and isinstance(ts, float)
        and (now - ts) < _CAPS_CACHE_TTL_SECONDS
    ):
        return cached  # type: ignore[return-value]

    try:
        body = await get_casparser_client().credits()
        features = {str(f) for f in (body.get("enabled_features") or [])}
        caps = CamsCapabilitiesResponse(
            parse_available=_FEATURE_PARSER in features,
            mailback_available=_FEATURE_GENERATOR in features,
        )
    except CasParserApiError as exc:
        # Can't read the feature list right now — assume the common shape
        # (parser yes, generator unknown→yes) and let the real calls surface
        # their own errors. Not cached, so recovery is immediate.
        logger.warning("CAS capabilities probe failed: %s", exc.short_reason)
        return CamsCapabilitiesResponse(parse_available=True, mailback_available=True)

    _caps_cache["data"] = caps
    _caps_cache["ts"] = now
    return caps


@router.post("/cams-request", response_model=CamsStatementRequestResponse)
async def request_cams_statement_email(
    payload: CamsStatementRequestBody,
    current_user: CurrentUser = Depends(get_effective_user),
):
    """Ask CAMS/KFintech to EMAIL the investor a detailed CAS PDF.

    The statement covers the investor's full history and is protected with the
    password chosen here; the user then uploads the received PDF to
    ``POST /mf-ingest/cams-pdf`` with the same password. Asynchronous on the
    registrar's side — the email usually arrives within a few minutes.
    """
    if not Settings.casparser_enabled():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Statement requests are unavailable on this server "
                "(the CAS Parser API key is not configured)."
            ),
        )

    now = time.monotonic()
    last = _cas_request_last_sent.get(current_user.id)
    if last is not None and (now - last) < _CAS_REQUEST_COOLDOWN_SECONDS:
        wait = int(_CAS_REQUEST_COOLDOWN_SECONDS - (now - last)) + 1
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"A statement was just requested — wait {wait}s before requesting "
                "another (the first email may still be on its way)."
            ),
        )

    try:
        result = await get_casparser_client().generate_kfintech_cas(
            email=payload.email,
            password=payload.password,
            from_date=_CAS_REQUEST_FROM_DATE,
            to_date=date.today().isoformat(),
            pan_no=payload.pan_no,
        )
    except CasParserApiError as exc:
        logger.warning("CAS mailback request failed: %s", exc.short_reason)
        # 402/403 = our CAS Parser subscription lacks credits / the generator
        # feature — a permanent config/plan problem, not a transient outage.
        # Tell the user to use the upload path instead of "try again later".
        if exc.status_code in (402, 403):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    "Statement-by-email isn't enabled on our statement service "
                    "right now. Please generate the statement on the CAMS "
                    "website (or use a CAS PDF you already have) and upload it "
                    "here instead."
                ),
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=(
                "Couldn't reach the statement service. Please try again in a "
                "minute, or generate the statement manually on the CAMS website."
            ),
        ) from exc

    if str(result.get("status") or "").strip().lower() == "failed":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(
                result.get("msg")
                or "The registrar rejected the statement request. Check the "
                "email and PAN and try again."
            ),
        )

    _cas_request_last_sent[current_user.id] = now
    return CamsStatementRequestResponse(
        status="success",
        email=payload.email,
        message=(
            "Statement requested. CAMS/KFintech will email the PDF to "
            f"{payload.email} within a few minutes — upload it here with the "
            "password you just set."
        ),
    )


@router.post("/cams-pdf", response_model=CamsPdfImportResponse)
async def ingest_cams_statement_pdf(
    background: BackgroundTasks,
    file: UploadFile = File(
        ..., description="CAMS / KFintech Consolidated Account Statement PDF"
    ),
    password: str = Form(
        ...,
        description="Password set when generating the CAS (commonly your PAN in capitals).",
    ),
    replace_existing: bool = Form(
        False,
        description=(
            "When true, wipe all CAMS-derived data (transactions, MF holdings, allocations, "
            "net-worth history) for the user before ingesting, so this statement fully "
            "replaces the old one. Default false appends/merges incrementally."
        ),
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
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The uploaded file is empty.",
        )
    if len(data) > _MAX_CAS_PDF_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="The PDF is too large (limit 20 MB).",
        )
    if not (password or "").strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A PDF password is required.",
        )

    try:
        result = await ingest_cams_pdf(
            db,
            current_user.id,
            file_bytes=data,
            password=password,
            source_filename=filename or None,
            replace_existing=replace_existing,
        )
    except CamsPdfParseError as exc:
        raise HTTPException(
            status_code=(
                status.HTTP_400_BAD_REQUEST
                if exc.bad_password
                else status.HTTP_422_UNPROCESSABLE_ENTITY
            ),
            detail=str(exc),
        ) from exc

    await maybe_recalculate_effective_risk(db, current_user.id, "cams_pdf_ingest")
    await db.commit()

    # Auto-build the real net-worth history (NAV fetch + daily series) so the
    # dashboard chart populates without the user tapping "Fetch Net Worth History".
    # Idempotent: skipped if a build is already pending/running. Best-effort —
    # never fail the upload because the background kickoff couldn't be queued.
    if result.status != "FAILED" and result.mf_transactions_inserted > 0:
        try:
            if await has_running_job(db, current_user.id) is None:
                job = await create_job(db, current_user.id)
                background.add_task(run_networth_backfill, current_user.id, job.id)
        except Exception:  # noqa: BLE001
            logger.exception("could not auto-start net-worth backfill after CAS upload")

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


@router.get("/cas-documents", response_model=CasDocumentListResponse)
async def list_cas_documents(
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    """The user's archived CAS statements (newest first) — the "My CAS
    statements" list on the profile. Records survive the per-upload data
    reset; the PDFs live in the private S3 bucket."""
    rows = (
        (
            await db.execute(
                select(UserCasDocument)
                .where(UserCasDocument.user_id == current_user.id)
                .order_by(UserCasDocument.uploaded_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return CasDocumentListResponse(
        documents=[CasDocumentItem.model_validate(r) for r in rows]
    )


async def _get_owned_cas_document(
    db: AsyncSession, doc_id: uuid.UUID, user_id: uuid.UUID
) -> UserCasDocument:
    row = (
        await db.execute(
            select(UserCasDocument).where(
                UserCasDocument.id == doc_id,
                UserCasDocument.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Statement not found.",
        )
    return row


@router.get(
    "/cas-documents/{doc_id}/download", response_model=CasDocumentDownloadResponse
)
async def download_cas_document(
    doc_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    """Short-lived (5 min) presigned URL for one of the user's own statements.
    The PDF is still protected by the user's statement password."""
    row = await _get_owned_cas_document(db, doc_id, current_user.id)
    if not stage_enabled():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Statement storage is not configured on this server "
                "(missing S3 settings)."
            ),
        )
    try:
        url = await presign_cas_download(row.s3_key, row.source_filename)
    except Exception as exc:  # noqa: BLE001 — misconfig (creds/region) or S3 outage
        logger.exception("presigning CAS download failed for %s", doc_id)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Statement storage is temporarily unavailable on this server. "
                "Please try again shortly."
            ),
        ) from exc
    return CasDocumentDownloadResponse(url=url, expires_in_seconds=300)


@router.delete("/cas-documents/{doc_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_cas_document(
    doc_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    """Remove an archived statement (S3 object + profile record)."""
    row = await _get_owned_cas_document(db, doc_id, current_user.id)
    if stage_enabled():
        try:
            await delete_cas_object(row.s3_key)
        except Exception:  # noqa: BLE001 — the DB row is the source of truth
            logger.warning("could not delete archived CAS object for %s", doc_id)
    await db.delete(row)
    await db.commit()


@router.post("/normalize/{import_id}", response_model=MfAaNormalizeOneResponse)
async def normalize_import(
    import_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    aa_import = await get_import_for_user(db, current_user.id, import_id)
    if not aa_import:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="AA import not found"
        )
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
    from app.core.database import _get_session_factory

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
