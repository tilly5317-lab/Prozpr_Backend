"""FastAPI router — `mfc_cas_router.py`.

The MF Central CAS consent flow: the replacement for asking the investor to
generate, download, and upload a password-protected CAS PDF.

    GET  /mfc-cas/config       is this server wired to MFC, and how to present it
    POST /mfc-cas/start        register the request, get the investor's redirect URL
    POST /mfc-cas/validate-qr  redeem the downloaded QR -> statement -> portfolio
    GET  /mfc-cas/requests     the user's consent attempts and what came of them

The middle of the flow is not ours: the investor leaves for MFC's site, receives
an OTP, chooses Summary or Detailed, and downloads a QR image. Nothing here can
observe that, which is why ``/start`` returns and the flow resumes only when a
QR arrives at ``/validate-qr``.
"""

from __future__ import annotations

import logging
import time
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.database import get_db
from app.core.dependencies import CurrentUser, get_effective_user
from app.domains.identity.models.user import User
from app.domains.ingestion.schemas import (
    MfcConfigResponse,
    MfcImportResponse,
    MfcIngestSummary,
    MfcRequestItem,
    MfcRequestListResponse,
    MfcStartRequest,
    MfcStartResponse,
    MfcValidateQrRequest,
)
from app.domains.ingestion.services.mfc_cas_ingest import (
    MfcFlowError,
    import_from_qr,
    list_requests,
    start_cas_request,
)
from app.domains.portfolio.services.networth_history_service import (
    create_job,
    has_running_job,
    run_networth_backfill,
)
from app.domains.profile.services._effective_risk import (
    maybe_recalculate_effective_risk,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/mfc-cas", tags=["MF Central CAS"])

# Per-user cooldown on /start. Every call consumes a billed MFC API hit and
# sends the investor a real OTP; a double-tap should not do either twice.
_START_COOLDOWN_SECONDS = 30.0
_start_last_called: dict[uuid.UUID, float] = {}

# The QR PNG is small (MFC's samples are ~1 KB). This cap is about rejecting a
# mistaken photo upload cheaply, not about MFC's limits.
_MAX_QR_BASE64_CHARS = 2_000_000


def _require_enabled() -> None:
    if not Settings.mfc_enabled():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "MF Central import is not configured on this server. Set the "
                "MFC_* credentials, or use the CAS PDF upload instead."
            ),
        )


def _flow_error(exc: MfcFlowError) -> HTTPException:
    """Map a flow failure onto a status the frontend already knows how to treat.

    A retryable failure is 502 (the frontend offers "try again"); everything
    else is 400/422, which it renders as a message and does not retry — the
    distinction matters because a retried QR is a burnt QR.
    """
    if exc.stage == "config":
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        )
    if exc.retryable:
        return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))
    if exc.stage in ("pan", "contact", "qr"):
        return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
    )


@router.get("/config", response_model=MfcConfigResponse)
async def mfc_config(current_user: CurrentUser = Depends(get_effective_user)):
    """Whether MF Central import is available here, and how to present it.

    Called on mount so the import screen can offer the MFC path or fall back to
    the PDF upload without a failed request in between.
    """
    base_url = Settings.get_mfc_api_base_url()
    if Settings.mfc_mock_enabled():
        environment = "mock"
    elif "uat" in base_url or "sit" in base_url:
        environment = "uat"
    else:
        environment = "production"
    return MfcConfigResponse(
        enabled=Settings.mfc_enabled(),
        environment=environment,
        redirect_url=Settings.get_mfc_redirect_url(),
        mfc_origin=Settings.get_mfc_redirect_base_url(),
        integration_mode="popup",
    )


@router.post("/start", response_model=MfcStartResponse)
async def start_mfc_cas(
    payload: MfcStartRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    """Register a CAS request with MF Central and return the investor's redirect URL.

    The URL carries an encrypted payload MFC decrypts on arrival — it is
    single-use, tied to one ``reqId``, and must not be cached or shared.
    """
    _require_enabled()

    now = time.monotonic()
    last = _start_last_called.get(current_user.id)
    if last is not None and (now - last) < _START_COOLDOWN_SECONDS:
        wait = int(_START_COOLDOWN_SECONDS - (now - last)) + 1
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"A request was just started — wait {wait}s. Check for the OTP "
                "from MF Central first."
            ),
        )

    try:
        result = await start_cas_request(
            db,
            current_user.id,
            pan=payload.pan_no,
            mobile=payload.mobile,
            email=payload.email,
            to_date=payload.to_date,
        )
    except MfcFlowError as exc:
        raise _flow_error(exc) from exc

    _start_last_called[current_user.id] = now
    return MfcStartResponse(
        request_id=result.request_id,
        client_ref_no=result.client_ref_no,
        req_id=result.req_id,
        otp_ref=result.otp_ref,
        redirect_url=result.redirect_url,
        pan_masked=result.pan_masked,
        from_date=result.from_date,
        to_date=result.to_date,
        otp_destination=result.otp_destination,
        message=(
            f"MF Central is ready for PAN {result.pan_masked}. Continue to their "
            f"site, enter the OTP sent to {result.otp_destination}, choose the "
            "DETAILED statement, and download the QR code."
        ),
    )


@router.post("/validate-qr", response_model=MfcImportResponse)
async def validate_mfc_qr(
    payload: MfcValidateQrRequest,
    background: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    """Exchange the QR the investor downloaded for their statement, and import it.

    The QR is single-use: MFC invalidates it on the first successful exchange,
    so a client must not retry this call on a business rejection. On success the
    statement lands through the same pipeline a PDF upload uses — new snapshot,
    superseding the last one — and the net-worth history rebuild is queued.
    """
    _require_enabled()

    if len(payload.qr_code or "") > _MAX_QR_BASE64_CHARS:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=(
                "That image is far larger than an MF Central QR code. Please "
                "upload the PNG they gave you rather than a photo of it."
            ),
        )

    try:
        result = await import_from_qr(
            db,
            current_user.id,
            qr_code=payload.qr_code,
            request_id=payload.request_id,
            req_id=payload.req_id,
        )
    except MfcFlowError as exc:
        raise _flow_error(exc) from exc

    ingest = result.ingest
    if ingest is None:
        # Readable but not ingestible. Still a 200: the investor's consent
        # succeeded and the payload is real, so the screen shows the data and
        # explains why the portfolio wasn't rebuilt from it.
        return MfcImportResponse(
            request_id=result.request_id,
            req_id=result.req_id,
            variant=result.variant,
            ingest=None,
            rejection=result.rejection,
            data=result.display,
            message=result.rejection or "The statement could not be imported.",
        )

    await maybe_recalculate_effective_risk(db, current_user.id, "mfc_cas_import")
    # The user now HAS a statement, so any earlier "I'll do this later" on the
    # onboarding CAMS step is moot — same reasoning as the PDF upload path.
    await db.execute(
        update(User)
        .where(User.id == current_user.id, User.cams_skipped_at.is_not(None))
        .values(cams_skipped_at=None)
    )
    await db.commit()

    if ingest.mf_transactions_inserted > 0:
        try:
            if await has_running_job(db, current_user.id) is None:
                job = await create_job(db, current_user.id)
                background.add_task(run_networth_backfill, current_user.id, job.id)
        except Exception:  # noqa: BLE001 — never fail an import over the kickoff
            logger.exception("could not auto-start net-worth backfill after MFC import")

    message = (
        f"Imported {ingest.schemes} scheme(s) across {ingest.folios} folio(s) "
        f"straight from MF Central; {ingest.mf_transactions_inserted} "
        f"transaction(s) added ({ingest.mf_transactions_skipped_duplicate} "
        f"duplicate(s) skipped). Portfolio value updated to "
        f"INR {ingest.total_value_inr:,.2f}."
    )
    if ingest.profile_fields_filled:
        message += (
            " Filled your profile from the statement: "
            + ", ".join(ingest.profile_fields_filled).replace("_", " ")
            + "."
        )

    return MfcImportResponse(
        request_id=result.request_id,
        req_id=result.req_id,
        variant=result.variant,
        ingest=MfcIngestSummary(
            import_id=ingest.import_id,
            cas_upload_id=ingest.cas_upload_id,
            status=ingest.status,
            cas_type=ingest.cas_type,
            statement_period_from=ingest.statement_period_from,
            statement_period_to=ingest.statement_period_to,
            folios=ingest.folios,
            schemes=ingest.schemes,
            aa_transactions_parsed=ingest.aa_transactions_parsed,
            mf_transactions_inserted=ingest.mf_transactions_inserted,
            mf_transactions_skipped_duplicate=ingest.mf_transactions_skipped_duplicate,
            portfolio_allocation_rows=ingest.portfolio_allocation_rows,
            total_value_inr=ingest.total_value_inr,
            normalize_error=ingest.normalize_error,
            profile_fields_filled=list(ingest.profile_fields_filled or []),
            reused_existing=bool(getattr(ingest, "reused_existing", False)),
        ),
        rejection=None,
        data=result.display,
        message=message,
    )


@router.get("/requests", response_model=MfcRequestListResponse)
async def list_mfc_requests(
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    """The user's MF Central consent attempts, newest first.

    Most rows rest at ``initiated``: we cannot see whether an investor finished
    on MFC's site, so an abandoned consent looks exactly like one still in
    progress. That ambiguity is the flow's, not a gap in the record.
    """
    rows = await list_requests(db, current_user.id, limit=limit)
    return MfcRequestListResponse(
        requests=[MfcRequestItem.model_validate(r) for r in rows]
    )
