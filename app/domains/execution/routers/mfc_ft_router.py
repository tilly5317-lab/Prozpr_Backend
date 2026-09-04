"""FastAPI router — `mfc_ft_router.py`.

MF Central Financial Transactions — the OUTBOUND half of the MFC integration.
``/mfc-cas`` reads the investor's portfolio out of the registrars; this writes
orders back to them.

    GET  /mfc-ft/masters                MFC's AMC / frequency / kind code tables
    POST /mfc-ft/orders                 place one transaction  -> reqId
    POST /mfc-ft/orders/{id}/otp        ask MFC to text the investor
    POST /mfc-ft/orders/{id}/consent    relay the OTP          -> executing
    GET  /mfc-ft/orders/{id}/status     poll the registrar's verdict
    POST /mfc-ft/orders/{id}/payment    confirm funding (purchases only)
    POST /mfc-ft/pause-cancel/validate  dry-run a SIP pause/cancel
    GET  /mfc-ft/orders                 the caller's orders

The four-call chain is exposed as four endpoints rather than hidden behind one,
because the investor types an OTP in the middle of it. A single "place order"
call would have to block for as long as a person takes to read an SMS, and
would leave the caller nothing to poll if they closed the tab.

Every route is scoped to the authenticated user, and the PAN is pinned to the
account's whenever we hold one — an order is money moving out of somebody's
folios, so "whose" is not a field a client gets to assert.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.database import get_db
from app.core.dependencies import CurrentUser, get_effective_user
from app.domains.execution.models.mfc_ft_order import (
    MFC_FT_TERMINAL,
    MfcFtKind,
    MfcFtOrder,
    MfcFtStatus,
)
from app.domains.execution.schemas.mfc_ft_schemas import (
    MfcFtOrderItem,
    MfcFtOrderListResponse,
    MfcFtOrderRequest,
    MfcFtOrderResponse,
    MfcFtOtpRequest,
    MfcFtPaymentRequest,
    MfcFtStatusResponse,
    MfcFtValidateResponse,
    MfcMastersResponse,
)
from app.domains.execution.services import mfc_ft_service
from app.domains.execution.services.mfc_ft_service import (
    BankLeg,
    MfcFtError,
    SchemeLeg,
)
from app.domains.execution.services.mfc_masters import (
    ACCOUNT_TYPE_MASTER,
    AMC_MASTER,
    FREQUENCY_MASTER,
    amc_name,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/mfc-ft", tags=["MF Central FT"])


def _require_enabled() -> None:
    if not Settings.mfc_enabled():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "MF Central execution is not configured on this server. Set the "
                "MFC_* credentials to enable it."
            ),
        )


def _ft_error(exc: MfcFtError) -> HTTPException:
    """Map a flow failure to a status the client can act on.

    Retryable failures are 502 so a client may offer "try again"; business
    rejections are 400/422 and must NOT be retried — MFC may already hold the
    order, and a second submission is a second order.
    """
    if exc.stage == "config":
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        )
    if exc.stage == "lookup":
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if exc.retryable:
        return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))
    # "rejected" is MFC's own "valid business error" — their status table calls
    # that 400, and it is emphatically not retryable.
    if exc.stage in (
        "validate",
        "pan",
        "contact",
        "amc",
        "state",
        "investorconsent",
        "rejected",
    ):
        return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
    )


def _item(order: MfcFtOrder) -> MfcFtOrderItem:
    item = MfcFtOrderItem.model_validate(order)
    # The code is what MFC wants; the name is what a person reads. Resolved on
    # the way out rather than stored, so a master update reaches old rows too.
    item.amc_name = amc_name(order.amc)
    return item


def _next_step(order: MfcFtOrder) -> str:
    if order.status in MFC_FT_TERMINAL:
        return "none"
    if order.status == MfcFtStatus.SUBMITTED.value:
        return "otp"
    if order.status == MfcFtStatus.OTP_SENT.value:
        return "consent"
    if order.status == MfcFtStatus.CONSENTED.value:
        return (
            "payment"
            if order.kind in (MfcFtKind.PURCHASE.value, MfcFtKind.ADDITIONAL.value)
            else "status"
        )
    return "status"


def _leg(payload: MfcFtOrderRequest) -> SchemeLeg:
    return SchemeLeg(
        isin=payload.isin,
        to_isin=payload.to_isin,
        folio=payload.folio,
        amount=payload.amount,
        units=payload.units,
        all_units=payload.all_units,
        scheme_name=payload.scheme_name,
        frequency=payload.frequency,
        start_date=payload.start_date,
        end_date=payload.end_date,
        installments=payload.installments,
        user_trxn_no=payload.user_trxn_no,
        pause_installments=payload.pause_installments,
        dist_id=payload.dist_id,
        sub_broker_arn=payload.sub_broker_arn,
        euin=payload.euin,
        ria_code=payload.ria_code,
        reinvest=payload.reinvest,
    )


def _bank(payload: MfcFtOrderRequest) -> BankLeg | None:
    if payload.bank is None:
        return None
    return BankLeg(
        account_no=payload.bank.account_no,
        account_type=payload.bank.account_type,
        name=payload.bank.name,
        branch=payload.bank.branch,
        city=payload.bank.city,
        pincode=payload.bank.pincode,
        ifsc=payload.bank.ifsc,
        neft_ifsc=payload.bank.neft_ifsc,
        micr=payload.bank.micr,
    )


@router.get("/masters", response_model=MfcMastersResponse)
async def ft_masters(current_user: CurrentUser = Depends(get_effective_user)):
    """MFC's code tables.

    Served rather than duplicated in a client bundle: MFC extends these when a
    fund house onboards, and a stale copy would reject a real AMC.
    """
    return MfcMastersResponse(
        amcs=[
            {"code": code, "name": full, "short_name": short}
            for code, (full, short) in AMC_MASTER.items()
        ],
        frequencies=[
            {"code": code, "label": label} for code, label in FREQUENCY_MASTER.items()
        ],
        account_types=[
            {"code": code, "label": label}
            for code, label in ACCOUNT_TYPE_MASTER.items()
        ],
        transaction_kinds=[
            {"code": k.value, "label": label}
            for k, label in (
                (MfcFtKind.PURCHASE, "Fresh purchase or new SIP"),
                (MfcFtKind.ADDITIONAL, "Additional purchase or additional SIP"),
                (MfcFtKind.REDEEM, "Redemption"),
                (MfcFtKind.SWITCH, "Switch"),
                (MfcFtKind.STP, "Systematic transfer plan"),
                (MfcFtKind.SWP, "Systematic withdrawal plan"),
                (MfcFtKind.SIP_PAUSE, "Pause a running SIP"),
                (MfcFtKind.SIP_CANCEL, "Cancel a running SIP"),
            )
        ],
    )


@router.post("/orders", response_model=MfcFtOrderResponse)
async def place_order(
    payload: MfcFtOrderRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    """Place one financial transaction with MF Central.

    Nothing executes yet. This registers the order and returns MFC's ``reqId``;
    the investor still has to receive and confirm an OTP, and the registrar's
    verdict only arrives via ``GET /orders/{id}/status``.
    """
    _require_enabled()
    kind = MfcFtKind(payload.kind)
    try:
        order = await mfc_ft_service.place_order(
            db,
            current_user.id,
            kind=kind,
            leg=_leg(payload),
            amc=payload.amc,
            bank=_bank(payload),
            pan=payload.pan_no,
            mobile=payload.mobile,
            email=payload.email,
            reason=payload.reason,
            reason_code=payload.reason_code,
        )
    except MfcFtError as exc:
        raise _ft_error(exc) from exc

    return MfcFtOrderResponse(
        order=_item(order),
        message=(
            f"MF Central accepted the order (reference {order.req_id}). "
            "Request the OTP to confirm it — nothing executes until the "
            "investor consents."
        ),
        next_step=_next_step(order),
    )


@router.post("/orders/{order_id}/otp", response_model=MfcFtOrderResponse)
async def request_otp(
    order_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    """Ask MF Central to send the investor the transaction OTP."""
    _require_enabled()
    try:
        order = await mfc_ft_service.get_order(db, current_user.id, order_id)
        order = await mfc_ft_service.send_otp(db, order)
    except MfcFtError as exc:
        raise _ft_error(exc) from exc

    return MfcFtOrderResponse(
        order=_item(order),
        message=(
            f"MF Central sent an OTP to {order.otp_destination or 'the registered contact'}."
        ),
        next_step=_next_step(order),
    )


@router.post("/orders/{order_id}/consent", response_model=MfcFtOrderResponse)
async def confirm_otp(
    order_id: uuid.UUID,
    payload: MfcFtOtpRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    """Relay the investor's OTP — the point at which the order goes to the RTA.

    A wrong code is a 400 and leaves the order confirmable: MFC accepts a retry
    against the same OTP reference, and failing the order over a typo would
    strand it.
    """
    _require_enabled()
    try:
        order = await mfc_ft_service.get_order(db, current_user.id, order_id)
        order = await mfc_ft_service.confirm_otp(db, order, payload.otp)
    except MfcFtError as exc:
        raise _ft_error(exc) from exc

    return MfcFtOrderResponse(
        order=_item(order),
        message=(
            "Consent recorded. The registrar processes the order asynchronously "
            "— poll the status endpoint for the outcome."
        ),
        next_step=_next_step(order),
    )


@router.get("/orders/{order_id}/status", response_model=MfcFtStatusResponse)
async def order_status(
    order_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    """Poll the registrar's own verdict.

    MFC documents no limit on this call, and it is the only source of an
    order's real outcome — every earlier step reports that MFC accepted a
    request, which is not the same as an AMC having allotted units.
    """
    _require_enabled()
    try:
        order = await mfc_ft_service.get_order(db, current_user.id, order_id)
        order = await mfc_ft_service.refresh_status(db, order)
    except MfcFtError as exc:
        raise _ft_error(exc) from exc

    return MfcFtStatusResponse(order=_item(order), raw=order.status_payload or {})


@router.post("/orders/{order_id}/payment", response_model=MfcFtOrderResponse)
async def confirm_payment(
    order_id: uuid.UUID,
    payload: MfcFtPaymentRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    """Tell MF Central the money moved. Purchases and SIP registrations only.

    Not optional bookkeeping: without it the registrar holds the order unfunded.
    """
    _require_enabled()
    try:
        order = await mfc_ft_service.get_order(db, current_user.id, order_id)
        order = await mfc_ft_service.update_payment(
            db,
            order,
            status=payload.status,
            bank_code=payload.bank_code,
            umrn=payload.umrn,
            mandate_ref_id=payload.mandate_ref_id,
            error_description=payload.error_description,
        )
    except MfcFtError as exc:
        raise _ft_error(exc) from exc

    return MfcFtOrderResponse(
        order=_item(order),
        message="Payment status sent to MF Central.",
        next_step=_next_step(order),
    )


@router.post("/pause-cancel/validate", response_model=MfcFtValidateResponse)
async def validate_pause_cancel(
    payload: MfcFtOrderRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    """Dry-run a SIP pause or cancel before committing the investor to an OTP.

    Writes nothing. The only FT family with a pre-flight, because a wrong
    ``user_trxn_no`` would otherwise be discovered only after the investor had
    already been sent a code.
    """
    _require_enabled()
    kind = MfcFtKind(payload.kind)
    if kind not in (MfcFtKind.SIP_PAUSE, MfcFtKind.SIP_CANCEL):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only sip_pause and sip_cancel can be validated ahead of time.",
        )
    try:
        raw = await mfc_ft_service.validate_pause_cancel(
            db,
            current_user.id,
            kind=kind,
            leg=_leg(payload),
            amc=payload.amc,
            pan=payload.pan_no,
            mobile=payload.mobile,
            email=payload.email,
        )
    except MfcFtError as exc:
        raise _ft_error(exc) from exc

    from app.domains.execution.services.mfc_ft_client import extract_errors

    problems = extract_errors(raw)
    return MfcFtValidateResponse(
        ok=not problems,
        raw=raw,
        message=(
            str(problems[0].get("message") or "This SIP cannot be changed.")
            if problems
            else "This SIP can be changed. Place the order to continue."
        ),
    )


@router.get("/orders", response_model=MfcFtOrderListResponse)
async def list_orders(
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    """The caller's MF Central orders, newest first."""
    rows = await mfc_ft_service.list_orders(db, current_user.id, limit=limit)
    return MfcFtOrderListResponse(orders=[_item(r) for r in rows])
