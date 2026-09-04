"""Application service — `mfc_cas_ingest.py`.

Drives the MF Central consent flow end to end and lands the resulting statement
through the existing CAS pipeline.

Two calls, minutes apart, with the investor off our site in between:

``start_cas_request`` — asks MFC for a ``reqId``/``otpRef``, records the attempt
in ``mfc_cas_requests``, and returns the encrypted redirect URL that carries the
investor into MFC's OTP + consent UI.

``import_from_qr`` — takes the QR image the investor downloaded, exchanges it
with MFC for the statement, maps it onto the legacy parsed-CAS dict
(:mod:`mfc_cas_adapter`) and hands it to
:func:`~app.domains.ingestion.services.cams_cas_ingest.ingest_parsed_cas`. From
that point the statement is indistinguishable from an uploaded PDF: same audit
rows, same snapshot versioning, same transaction-derived holdings.

What this flow does NOT do, deliberately:

* It does not poll. There is no MFC endpoint that reports whether the investor
  finished consenting, so the QR arriving back is the only completion signal.
* It does not retry ``validateQRCode``. The QR is single-use; a retry after a
  business rejection burns the investor's consent and forces a fresh OTP.

The PAN is taken from the authenticated user's own record wherever one exists,
never from the request body, for the same reason the CAMS mailback pins it: the
PAN decides whose statement MFC assembles.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.domains.identity.models.user import User
from app.domains.ingestion.models import MfcCasRequest, MfcCasRequestStatus
from app.domains.ingestion.services.cams_cas_ingest import (
    CamsIngestResult,
    CamsPdfParseError,
    ingest_parsed_cas,
)
from app.domains.ingestion.services.casparser_adapter import CasResponseShapeError
from app.domains.ingestion.services.mfc_cas_adapter import (
    summarize_for_display,
    to_legacy_parsed,
)
from app.domains.ingestion.services.mfc_client import (
    MfcApiError,
    MfcConfigError,
    get_mfc_client,
)
from app.domains.ingestion.services.mfc_crypto import MfcCryptoError

logger = logging.getLogger(__name__)

_PAN_RE = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")

# MFC caps clientRefNo at 30 characters and requires global uniqueness. A short
# prefix plus 20 hex characters of a fresh uuid4 fits with room to spare and
# stays greppable in a support thread with them.
_CLIENT_REF_PREFIX = "prozpr"

# MFC wants DD-MMM-YYYY and treats fromDate/toDate as the ledger window. An early
# fromDate simply returns everything the investor holds, which is what a
# portfolio rebuild needs — a narrower window silently truncates cost basis.
_LEDGER_FROM_DATE = "01-Apr-1990"


class MfcFlowError(Exception):
    """A step of the MFC flow failed in a way worth showing the investor.

    ``retryable`` distinguishes "MFC was unreachable, try again" from "MFC said
    no", because the second is a dead end for this QR and telling the user to
    retry it wastes their consent.
    """

    def __init__(
        self, message: str, *, retryable: bool = False, stage: str | None = None
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.stage = stage


@dataclass(frozen=True)
class MfcStartResult:
    """What the frontend needs to send the investor to MFC."""

    request_id: uuid.UUID
    client_ref_no: str
    req_id: str
    otp_ref: str
    redirect_url: str
    pan_masked: str
    from_date: str
    to_date: str
    # Where MFC will send the consent OTP, masked. The investor needs to know
    # which inbox or handset to watch, and it is frequently NOT the one they
    # signed in with — the contact registered with the fund houses wins.
    otp_destination: str


@dataclass(frozen=True)
class MfcImportResult:
    """The outcome of exchanging one QR."""

    request_id: uuid.UUID
    req_id: str
    variant: str
    ingest: Optional[CamsIngestResult]
    display: dict[str, Any] = field(default_factory=dict)
    # Set when the statement was readable but not ingestible (a Summary CAS, or
    # a portfolio worth nothing). The display payload is still returned, so the
    # investor sees what they consented to alongside the reason it can't be used.
    rejection: Optional[str] = None


# --------------------------------------------------------------------------- helpers


def _new_client_ref_no() -> str:
    return f"{_CLIENT_REF_PREFIX}{uuid.uuid4().hex[:20]}"


def _mask_pan(pan: str) -> str:
    return f"{pan[:5]}****{pan[9:]}" if len(pan) >= 10 else "****"


def _mask_contact(mobile: Optional[str], email: Optional[str]) -> str:
    """A phrase naming where the OTP lands, without reprinting the contact.

    "your registered contact" is the honest fallback and not a placeholder: the
    caller may have supplied neither, in which case MFC uses whatever it holds
    against the PAN and we genuinely do not know.
    """
    if mobile:
        digits = re.sub(r"\D", "", mobile)
        return (
            f"your mobile ending {digits[-4:]}" if len(digits) >= 4 else "your mobile"
        )
    if email:
        name, _, domain = email.partition("@")
        head = name[:2] if len(name) > 2 else name[:1]
        return f"{head}{'*' * max(1, len(name) - len(head))}@{domain}"
    return "your registered contact"


def _normalize_qr(qr_code: str) -> str:
    """Accept a bare base64 PNG or a full ``data:image/png;base64,...`` URL.

    The frontend sends the bare form, but a user pasting from devtools or a
    native WebView bridge sends the data URL, and MFC rejects the prefix with a
    500 rather than a validation error.
    """
    text = (qr_code or "").strip()
    if not text:
        raise MfcFlowError("No QR code image was supplied.", stage="qr")
    if text.startswith("data:"):
        _, _, text = text.partition(",")
    text = "".join(text.split())
    try:
        decoded = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise MfcFlowError(
            "That QR code image could not be read. Please upload the PNG "
            "MF Central gave you, unedited.",
            stage="qr",
        ) from exc
    if len(decoded) < 100:
        raise MfcFlowError(
            "That file is too small to be the MF Central QR code.", stage="qr"
        )
    return text


async def _resolve_pan(
    db: AsyncSession, user_id: uuid.UUID, submitted: Optional[str]
) -> str:
    """The user's own PAN wins; a submitted one is only accepted when we hold none.

    Same rule as the CAMS mailback endpoint, and for the same reason: the PAN is
    what MFC keys the statement on, so a body-supplied PAN would let one account
    request another person's consolidated holdings. Before we know the caller's
    PAN, the submitted one is them asserting their own — the same trust level as
    onboarding.
    """
    stored = (
        await db.execute(select(User.pan).where(User.id == user_id))
    ).scalar_one_or_none()
    stored_pan = (stored or "").strip().upper() or None
    submitted_pan = (submitted or "").strip().upper() or None

    if stored_pan:
        if submitted_pan and submitted_pan != stored_pan:
            raise MfcFlowError(
                "That PAN does not match the one on your account. Statements can "
                "only be requested for your own PAN.",
                stage="pan",
            )
        pan = stored_pan
    else:
        pan = submitted_pan

    if not pan:
        raise MfcFlowError(
            "We need your PAN to request your statement from MF Central.",
            stage="pan",
        )
    if not _PAN_RE.match(pan):
        raise MfcFlowError(
            "That PAN is not in the expected format (ABCDE1234F).", stage="pan"
        )
    return pan


def _api_error_to_flow(exc: MfcApiError) -> MfcFlowError:
    """Turn an MFC transport/business failure into copy an investor can act on."""
    if isinstance(exc, MfcConfigError):
        return MfcFlowError(
            "MF Central import is not configured on this server.",
            stage="config",
        )
    status = exc.status_code
    if status in (401, 403):
        return MfcFlowError(
            "MF Central rejected our credentials. This is on our side — please "
            "try again shortly.",
            stage=exc.stage,
        )
    if status in (400, 422):
        return MfcFlowError(
            exc.short_reason
            or "MF Central could not process this request. Please check your PAN "
            "and contact details and try again.",
            stage=exc.stage,
        )
    return MfcFlowError(
        "Could not reach MF Central just now. Please try again in a minute.",
        retryable=True,
        stage=exc.stage,
    )


# --------------------------------------------------------------------------- step 1


async def start_cas_request(
    db: AsyncSession,
    user_id: uuid.UUID,
    *,
    pan: Optional[str] = None,
    mobile: Optional[str] = None,
    email: Optional[str] = None,
    to_date: Optional[str] = None,
) -> MfcStartResult:
    """Register a CAS request with MFC and build the investor's redirect URL.

    Exactly one of mobile/email is sent: MFC documents passing both as a
    rejection, and whichever is sent is where the consent OTP goes. Mobile is
    preferred when we have one, because the OTP is an SMS and arrives faster
    than the email variant during a live onboarding session.
    """
    resolved_pan = await _resolve_pan(db, user_id, pan)

    contact_mobile = (mobile or "").strip() or None
    contact_email = (email or "").strip().lower() or None
    if contact_mobile and contact_email:
        contact_email = None
    if not contact_mobile and not contact_email:
        row = (
            await db.execute(select(User.mobile, User.email).where(User.id == user_id))
        ).one_or_none()
        if row is not None:
            contact_mobile = (row[0] or "").strip() or None
            if not contact_mobile:
                contact_email = (row[1] or "").strip().lower() or None
    if not contact_mobile and not contact_email:
        raise MfcFlowError(
            "MF Central needs a mobile number or email registered with your "
            "mutual funds to send the consent OTP.",
            stage="contact",
        )
    if contact_mobile and not contact_mobile.startswith("+"):
        # MFC accepts a bare 10-digit number, but their samples all carry the
        # country code and the bare form has been seen to route the OTP nowhere.
        digits = re.sub(r"\D", "", contact_mobile)
        contact_mobile = f"+91{digits[-10:]}" if len(digits) >= 10 else contact_mobile

    client_ref_no = _new_client_ref_no()
    window_to = (to_date or "").strip() or date.today().strftime("%d-%b-%Y")

    try:
        client = get_mfc_client()
        response = await client.new_cas_request(
            client_ref_no=client_ref_no,
            pan=resolved_pan,
            mobile=contact_mobile,
            email=contact_email,
            from_date=_LEDGER_FROM_DATE,
            to_date=window_to,
            redirect_url=Settings.get_mfc_redirect_url(),
        )
    except MfcApiError as exc:
        logger.warning("MFC newCasRequest failed: %s", exc.short_reason)
        raise _api_error_to_flow(exc) from exc
    except MfcCryptoError as exc:
        logger.exception("MFC request could not be encrypted/signed")
        raise MfcFlowError(
            f"MF Central integration is misconfigured: {exc}", stage="crypto"
        ) from exc

    req_id = str(response.get("reqId") or "").strip()
    otp_ref = str(response.get("otpRef") or "").strip()

    row = MfcCasRequest(
        user_id=user_id,
        client_ref_no=client_ref_no,
        req_id=req_id,
        otp_ref=otp_ref or None,
        pan=resolved_pan,
        mobile=contact_mobile,
        email=contact_email,
        from_date=_LEDGER_FROM_DATE,
        to_date=window_to,
        status=MfcCasRequestStatus.INITIATED.value,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)

    try:
        redirect_url = client.build_redirect_url(
            req_id=req_id,
            otp_ref=otp_ref,
            client_ref_no=client_ref_no,
            pan=resolved_pan,
            email=contact_email,
            mobile=contact_mobile,
            redirect_url=Settings.get_mfc_redirect_url(),
            redirect_base_url=Settings.get_mfc_redirect_base_url(),
            url_encryption_key=Settings.get_mfc_url_encryption_key() or "",
        )
    except MfcCryptoError as exc:
        # The request exists at MFC either way, so record why we could not send
        # the investor to it rather than leaving a row that looks abandoned.
        row.status = MfcCasRequestStatus.FAILED.value
        row.error = f"redirect url: {exc}"
        await db.commit()
        raise MfcFlowError(
            f"MF Central redirect is misconfigured: {exc}", stage="crypto"
        ) from exc

    return MfcStartResult(
        request_id=row.id,
        client_ref_no=client_ref_no,
        req_id=req_id,
        otp_ref=otp_ref,
        redirect_url=redirect_url,
        pan_masked=_mask_pan(resolved_pan),
        from_date=_LEDGER_FROM_DATE,
        to_date=window_to,
        otp_destination=_mask_contact(contact_mobile, contact_email),
    )


# --------------------------------------------------------------------------- step 2


async def import_from_qr(
    db: AsyncSession,
    user_id: uuid.UUID,
    *,
    qr_code: str,
    request_id: Optional[uuid.UUID] = None,
    req_id: Optional[str] = None,
) -> MfcImportResult:
    """Exchange the investor's QR for their statement and ingest it.

    ``request_id`` (ours) is preferred over ``req_id`` (MFC's) because it is the
    one the frontend already holds and it cannot be guessed; either resolves to
    the same row, and both are scoped to the calling user so one account can
    never redeem another's consent.
    """
    row = await _load_request(db, user_id, request_id=request_id, req_id=req_id)
    qr = _normalize_qr(qr_code)

    try:
        payload = await get_mfc_client().validate_qr_code(
            req_id=row.req_id or "",
            client_ref_no=row.client_ref_no,
            qr_code_base64=qr,
        )
    except MfcApiError as exc:
        await _fail(db, row, f"validateQRCode: {exc.short_reason}")
        logger.warning("MFC validateQRCode failed: %s", exc.short_reason)
        raise _api_error_to_flow(exc) from exc
    except MfcCryptoError as exc:
        await _fail(db, row, f"crypto: {exc}")
        raise MfcFlowError(
            f"Could not read MF Central's response: {exc}", stage="crypto"
        ) from exc

    display = summarize_for_display(payload)
    row.cas_variant = display.get("variant")

    try:
        parsed = to_legacy_parsed(payload)
    except CasResponseShapeError as exc:
        await _fail(db, row, str(exc))
        raise MfcFlowError(str(exc), stage="shape") from exc

    # The snapshot dedupe key. MFC gives us no file, so the canonical bytes are
    # the payload itself, with reqId excluded: two consents over the same
    # holdings differ only by request id, and hashing that in would mint a fresh
    # snapshot for a statement identical to the live one.
    content_sha256 = _payload_digest(payload)

    try:
        result = await ingest_parsed_cas(
            db,
            user_id,
            parsed=parsed,
            content_sha256=content_sha256,
            source_filename=f"mfcentral-{row.req_id or row.client_ref_no}.json",
            file_bytes=None,
        )
    except CamsPdfParseError as exc:
        # Readable, but not something a portfolio can be built from — a Summary
        # statement or a zero-value one. Not a failure of the flow, so the row
        # records the reason and the investor still gets to see the data.
        row.status = MfcCasRequestStatus.FAILED.value
        row.error = str(exc)
        row.completed_at = datetime.now(timezone.utc)
        await db.commit()
        return MfcImportResult(
            request_id=row.id,
            req_id=row.req_id or "",
            variant=str(display.get("variant") or "unknown"),
            ingest=None,
            display=display,
            rejection=str(exc),
        )
    except Exception as exc:  # noqa: BLE001
        await _fail(db, row, repr(exc))
        raise

    counts = display.get("counts") or {}
    row.status = MfcCasRequestStatus.IMPORTED.value
    row.error = None
    row.folios = int(counts.get("folios") or 0) or result.folios
    row.schemes = result.schemes
    row.transactions = result.aa_transactions_parsed
    row.total_value_inr = result.total_value_inr
    row.cas_upload_id = result.cas_upload_id
    row.mf_aa_import_id = result.import_id
    row.completed_at = datetime.now(timezone.utc)
    await db.commit()

    return MfcImportResult(
        request_id=row.id,
        req_id=row.req_id or "",
        variant=str(display.get("variant") or "unknown"),
        ingest=result,
        display=display,
    )


def _payload_digest(payload: dict[str, Any]) -> str:
    """Stable hash of the statement's CONTENT, ignoring per-request identifiers.

    ``sort_keys`` matters: MFC does not guarantee key order, and an unordered
    dump would make the same holdings hash differently on consecutive consents,
    defeating the dedupe it exists to serve.
    """
    body = {k: v for k, v in payload.items() if k not in {"reqId", "clientRefNo"}}
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


async def _load_request(
    db: AsyncSession,
    user_id: uuid.UUID,
    *,
    request_id: Optional[uuid.UUID],
    req_id: Optional[str],
) -> MfcCasRequest:
    stmt = select(MfcCasRequest).where(MfcCasRequest.user_id == user_id)
    if request_id is not None:
        stmt = stmt.where(MfcCasRequest.id == request_id)
    elif req_id:
        stmt = stmt.where(MfcCasRequest.req_id == str(req_id))
    else:
        # No identifier: fall back to the newest request this user started. The
        # investor coming back from MFC in a fresh tab has lost the id, and
        # their most recent consent is the only one a QR could belong to.
        stmt = stmt.order_by(MfcCasRequest.created_at.desc()).limit(1)

    row = (await db.execute(stmt)).scalars().first()
    if row is None:
        raise MfcFlowError(
            "We couldn't find that MF Central request. Please start the import again.",
            stage="lookup",
        )
    if not row.req_id:
        raise MfcFlowError(
            "That MF Central request never completed. Please start it again.",
            stage="lookup",
        )
    return row


async def _fail(db: AsyncSession, row: MfcCasRequest, reason: str) -> None:
    row.status = MfcCasRequestStatus.FAILED.value
    row.error = reason[:2000]
    row.completed_at = datetime.now(timezone.utc)
    await db.commit()


# --------------------------------------------------------------------------- history


async def list_requests(
    db: AsyncSession, user_id: uuid.UUID, *, limit: int = 20
) -> list[MfcCasRequest]:
    """The user's MFC consent attempts, newest first."""
    return list(
        (
            await db.execute(
                select(MfcCasRequest)
                .where(MfcCasRequest.user_id == user_id)
                .order_by(MfcCasRequest.created_at.desc())
                .limit(max(1, min(limit, 100)))
            )
        )
        .scalars()
        .all()
    )
