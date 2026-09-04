"""Application service — `mfc_ft_service.py`.

Builds MF Central FT payloads, drives the four-call chain, and records every
order in ``mfc_ft_orders``.

MFC's eight transaction families share ONE envelope and differ only in the
per-scheme object inside it::

    {
      "reqId": "", "clientRefNo": ..., "pan": ..., "mobile"|"email": ...,
      "pekrn": "", "otherAPI": "<family code>",
      "data": [{
        "amc": "<code>",
        "schemeOptions": [ { ...the part that differs... } ],
        "otpMobile"|"otpEmail": ..., "otpSentTo": "M"|"E",
        "source": "WEB"
      }]
    }

So :func:`_envelope` is written once and each family contributes only its
scheme object. Doing it the other way — eight payload builders — is how the
``otpSentTo``/``otpMobile`` agreement, which MFC rejects with an unhelpful 400,
gets right in seven places and wrong in the eighth.

Ordering rule throughout: **persist before calling MFC, never after.** The row
is written with our ``clientRefNo`` before the request goes out, so an order
that MFC accepts but whose response we never see is still one we can ask them
about. The reverse order loses money we cannot name.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.execution.models.mfc_ft_order import (
    MFC_FT_TERMINAL,
    MfcFtKind,
    MfcFtOrder,
    MfcFtStatus,
)
from app.domains.execution.services import mfc_ft_client
from app.domains.execution.services.mfc_masters import (
    resolve_amc_code,
    resolve_frequency,
)
from app.domains.identity.models.user import User
from app.domains.ingestion.services.mfc_client import (
    MfcApiError,
    MfcConfigError,
)
from app.domains.ingestion.services.mfc_crypto import MfcCryptoError

logger = logging.getLogger(__name__)

_PAN_RE = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
_ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")

# MFC caps clientRefNo at 30 characters and demands global uniqueness.
_CLIENT_REF_PREFIX = "pzft"

# What we tell MFC the order came from. Their master lists "WEB"; it appears in
# the RTA's audit trail, so it is a constant rather than something per-caller.
_SOURCE = "WEB"


class MfcFtError(Exception):
    """An FT step failed in a way worth showing the investor."""

    def __init__(
        self, message: str, *, retryable: bool = False, stage: str | None = None
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.stage = stage


@dataclass(frozen=True)
class SchemeLeg:
    """One line of an order — the scheme, and what to do to it.

    Deliberately one flat shape for all eight families rather than eight typed
    ones: the fields are largely shared, MFC ignores what a family does not use,
    and a per-family class hierarchy would put the validation that actually
    matters (which fields a family REQUIRES) in eight places instead of one.
    """

    isin: Optional[str] = None
    to_isin: Optional[str] = None
    folio: Optional[str] = None
    amount: Optional[float] = None
    units: Optional[float] = None
    all_units: bool = False
    scheme_name: Optional[str] = None
    # SIP / STP / SWP
    frequency: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    installments: Optional[int] = None
    # Pause / cancel target — the RTA's own number for the running SIP.
    user_trxn_no: Optional[str] = None
    pause_installments: Optional[int] = None
    # Distribution
    dist_id: Optional[str] = None
    sub_broker_arn: Optional[str] = None
    euin: Optional[str] = None
    ria_code: Optional[str] = None
    reinvest: Optional[str] = None


@dataclass(frozen=True)
class BankLeg:
    """The bank account a redemption pays out to / a purchase debits.

    MFC requires it to be IMPS-verified or TPV'd on their side; we only carry
    it. Account numbers are never logged — see the model's note on the payload.
    """

    account_no: str
    account_type: str = "SB"
    name: Optional[str] = None
    branch: Optional[str] = None
    city: Optional[str] = None
    pincode: Optional[str] = None
    ifsc: Optional[str] = None
    neft_ifsc: Optional[str] = None
    micr: Optional[str] = None


# --------------------------------------------------------------------------- helpers


def _new_client_ref_no() -> str:
    return f"{_CLIENT_REF_PREFIX}{uuid.uuid4().hex[:24]}"


def _today() -> str:
    return date.today().strftime("%d-%b-%Y")


def _now_time() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _s(value: Any) -> str:
    """MFC's FT payloads are strings throughout, including the numbers.

    Their validator rejects a JSON number where it documents String(n) — so
    this is a wire-format requirement, not a style choice.
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Y" if value else "N"
    if isinstance(value, float):
        # Amounts are whole rupees in every FT sample MFC publishes, and their
        # purchase docs say "No decimals allowed" outright.
        return str(int(value)) if value.is_integer() else f"{value:.4f}".rstrip("0")
    return str(value).strip()


def _api_error_to_ft(exc: MfcApiError) -> MfcFtError:
    if isinstance(exc, MfcConfigError):
        return MfcFtError(
            "MF Central execution is not configured on this server.", stage="config"
        )
    if exc.status_code in (401, 403):
        return MfcFtError(
            "MF Central rejected our credentials. This is on our side — please "
            "try again shortly.",
            stage=exc.stage,
        )
    if exc.status_code in (400, 422):
        return MfcFtError(
            exc.short_reason or "MF Central could not process this order.",
            stage=exc.stage,
        )
    return MfcFtError(
        "Could not reach MF Central just now. Please try again in a minute.",
        retryable=True,
        stage=exc.stage,
    )


async def _resolve_investor(
    db: AsyncSession,
    user_id: uuid.UUID,
    *,
    pan: Optional[str],
    mobile: Optional[str],
    email: Optional[str],
) -> tuple[str, Optional[str], Optional[str]]:
    """(pan, mobile, email) for the order, with exactly one contact populated.

    The PAN is pinned to the account's whenever we hold one, for the same
    reason the CAS flow pins it — except here the consequence is not a leaked
    statement but an order placed against someone else's folios.
    """
    row = (
        await db.execute(
            select(User.pan, User.mobile, User.email).where(User.id == user_id)
        )
    ).one_or_none()
    stored_pan = ((row[0] if row else None) or "").strip().upper() or None
    submitted = (pan or "").strip().upper() or None

    if stored_pan:
        if submitted and submitted != stored_pan:
            raise MfcFtError(
                "That PAN does not match the one on your account. Orders can "
                "only be placed for your own PAN.",
                stage="pan",
            )
        effective_pan = stored_pan
    else:
        effective_pan = submitted
    if not effective_pan:
        raise MfcFtError("We need your PAN to place this order.", stage="pan")
    if not _PAN_RE.match(effective_pan):
        raise MfcFtError("That PAN is not in the expected format.", stage="pan")

    out_mobile = (mobile or "").strip() or None
    out_email = (email or "").strip().lower() or None
    if out_mobile and out_email:
        # MFC documents passing both as a rejection. Mobile wins: the OTP is an
        # SMS and lands faster than the email variant.
        out_email = None
    if not out_mobile and not out_email:
        out_mobile = ((row[1] if row else None) or "").strip() or None
        if not out_mobile:
            out_email = ((row[2] if row else None) or "").strip().lower() or None
    if not out_mobile and not out_email:
        raise MfcFtError(
            "MF Central needs a mobile or email registered with your funds to "
            "send the transaction OTP.",
            stage="contact",
        )
    if out_mobile and not out_mobile.startswith("+"):
        digits = re.sub(r"\D", "", out_mobile)
        if len(digits) >= 10:
            out_mobile = f"+91{digits[-10:]}"
    return effective_pan, out_mobile, out_email


def _envelope(
    *,
    kind: MfcFtKind,
    client_ref_no: str,
    pan: str,
    mobile: Optional[str],
    email: Optional[str],
    amc: str,
    scheme_options: list[dict[str, Any]],
    extra_block: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """The shared FT request body. One place, so the OTP fields agree once.

    ``otpSentTo`` must name the channel that is actually populated; MFC rejects
    the mismatch with a 400 that does not say which field it means.
    """
    block: dict[str, Any] = {
        "amc": amc,
        "schemeOptions": scheme_options,
        "otpEmail": email or "",
        "otpMobile": mobile or "",
        "otpSentTo": "M" if mobile else "E",
        "source": _SOURCE,
    }
    if extra_block:
        block.update(extra_block)
    return {
        "reqId": "",
        "clientRefNo": client_ref_no,
        "pan": pan,
        "mobile": mobile or "",
        "email": email or "",
        "pekrn": "",
        "otherAPI": mfc_ft_client.FT_OTHER_API[kind],
        "data": [block],
    }


def _validate(kind: MfcFtKind, leg: SchemeLeg) -> None:
    """Refuse an order MFC would reject, while we can still say why usefully.

    MFC's rejections name a field but not the rule, and they arrive AFTER the
    order exists on their side — so the cheap checks belong here.
    """
    if kind in (MfcFtKind.SIP_PAUSE, MfcFtKind.SIP_CANCEL):
        if not leg.user_trxn_no:
            raise MfcFtError(
                "Pausing or cancelling a SIP needs the registrar's transaction "
                "number for it.",
                stage="validate",
            )
        return

    if not leg.isin:
        raise MfcFtError("An ISIN is required.", stage="validate")
    if not _ISIN_RE.match(leg.isin.upper()):
        raise MfcFtError(f"'{leg.isin}' is not a valid ISIN.", stage="validate")

    if kind is MfcFtKind.SWITCH:
        if not leg.to_isin:
            raise MfcFtError(
                "A switch needs the scheme to switch INTO.", stage="validate"
            )
        if leg.to_isin.upper() == leg.isin.upper():
            raise MfcFtError(
                "The switch-in and switch-out schemes are the same.", stage="validate"
            )

    if kind is MfcFtKind.REDEEM:
        # MFC accepts amount, units, or "everything" — but exactly one, and
        # sending none redeems nothing while still consuming the investor's OTP.
        if not leg.all_units and not leg.amount and not leg.units:
            raise MfcFtError(
                "A redemption needs an amount, a unit count, or all units.",
                stage="validate",
            )
    elif kind in (
        MfcFtKind.PURCHASE,
        MfcFtKind.ADDITIONAL,
        MfcFtKind.STP,
        MfcFtKind.SWP,
    ):
        if not leg.amount or leg.amount <= 0:
            raise MfcFtError("An amount is required.", stage="validate")

    if kind in (MfcFtKind.STP, MfcFtKind.SWP):
        if not leg.frequency:
            raise MfcFtError("A frequency is required.", stage="validate")
        if not resolve_frequency(leg.frequency):
            raise MfcFtError(
                f"'{leg.frequency}' is not a cadence MF Central recognises.",
                stage="validate",
            )
        if not leg.start_date:
            raise MfcFtError("A start date is required.", stage="validate")
        if kind is MfcFtKind.STP and not leg.to_isin:
            raise MfcFtError(
                "An STP needs the scheme to transfer INTO.", stage="validate"
            )


def _scheme_options(
    kind: MfcFtKind, leg: SchemeLeg, bank: Optional[BankLeg]
) -> list[dict[str, Any]]:
    """The per-family scheme object. The only part that differs between them."""
    common = {
        "itemNo": "",
        "folio": _s(leg.folio),
        "trxnDate": _today(),
        "trxnTime": _now_time(),
        "userTrxnNo": _s(leg.user_trxn_no),
        "distId": _s(leg.dist_id),
        "subDist": "",
        "subBrokerARN": _s(leg.sub_broker_arn),
        "euin": _s(leg.euin),
        "euinDeclarationFlag": "Y" if leg.euin else "N",
        "riaCode": _s(leg.ria_code),
    }

    if kind is MfcFtKind.REDEEM:
        option = {
            **common,
            "isin": _s(leg.isin).upper(),
            "amount": _s(leg.amount),
            "units": _s(leg.units),
            "allUnits": "Y" if leg.all_units else "N",
            # Redemption uses MFC's one misspelt key. Correcting it to
            # userTrxnNo silently drops the value.
            "userTrxNo": _s(leg.user_trxn_no),
        }
        if bank:
            option["bank"] = _bank_block(bank)
        return [option]

    if kind is MfcFtKind.SWITCH:
        return [
            {
                **common,
                "fromIsin": _s(leg.isin).upper(),
                "toIsin": _s(leg.to_isin).upper(),
                "reInvest": _s(leg.reinvest),
                "amount": _s(leg.amount),
                "units": _s(leg.units),
                "allUnits": "Y" if leg.all_units else "N",
            }
        ]

    if kind in (MfcFtKind.STP, MfcFtKind.SWP):
        return [
            {
                **common,
                "fromIsin": _s(leg.isin).upper(),
                "toIsin": _s(leg.to_isin).upper(),
                "reInvest": _s(leg.reinvest),
                "amount": _s(leg.amount),
                "trxnType": "STP" if kind is MfcFtKind.STP else "SWP",
                "startDate": _s(leg.start_date),
                "endDate": _s(leg.end_date),
                "frequency": resolve_frequency(leg.frequency) or "",
                # The day-of-month the instalment runs. MFC derives it from the
                # start date when blank, which is what most callers want.
                "stpSwpDay": _day_of_month(leg.start_date),
                "variableType": "",
            }
        ]

    if kind in (MfcFtKind.SIP_PAUSE, MfcFtKind.SIP_CANCEL):
        return [
            {
                "itemNo": "",
                "folio": _s(leg.folio),
                "isin": _s(leg.isin).upper(),
                "schemeCode": "",
                "userTrxnNo": _s(leg.user_trxn_no),
                "trxnType": "PSIP" if kind is MfcFtKind.SIP_PAUSE else "CSIP",
                "pauseInstallments": _s(leg.pause_installments),
            }
        ]

    # PURCHASE / ADDITIONAL. A SIP is a purchase with a cadence — MFC has no
    # separate endpoint for one, which is why `frequency` decides trxnType.
    is_sip = bool(leg.frequency)
    if kind is MfcFtKind.PURCHASE:
        trxn_type = "SIP" if is_sip else "FP"
    else:
        trxn_type = "ASIP" if is_sip else "AP"
    return [
        {
            **common,
            "isin": _s(leg.isin).upper(),
            "amount": _s(leg.amount),
            "reInvest": _s(leg.reinvest) or "N",
            "trxnType": trxn_type,
            "startDate": _s(leg.start_date),
            "endDate": _s(leg.end_date),
            "frequency": (resolve_frequency(leg.frequency) or "") if is_sip else "",
            "noOfInstallments": _s(leg.installments),
            # MFC's samples set these to the first instalment for a SIP, and to
            # the purchase itself for a lumpsum.
            "registrationAmount": _s(leg.amount),
            "registrationDate": _s(leg.start_date) or _today(),
            "sipType": "",
        }
    ]


def _bank_block(bank: BankLeg) -> dict[str, Any]:
    return {
        "accountNo": _s(bank.account_no),
        "accountType": _s(bank.account_type).upper() or "SB",
        "name": _s(bank.name),
        "branch": _s(bank.branch),
        "city": _s(bank.city),
        "pincode": _s(bank.pincode),
        "ifsc": _s(bank.ifsc).upper(),
        "neftIfsc": _s(bank.neft_ifsc or bank.ifsc).upper(),
        "micr": _s(bank.micr),
        "default": "Y",
    }


def _day_of_month(start_date: Optional[str]) -> str:
    text = (start_date or "").strip()
    if not text:
        return ""
    for fmt in ("%d-%b-%Y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return str(datetime.strptime(text, fmt).day)
        except ValueError:
            continue
    return ""


# --------------------------------------------------------------------------- step 1


async def place_order(
    db: AsyncSession,
    user_id: uuid.UUID,
    *,
    kind: MfcFtKind,
    leg: SchemeLeg,
    amc: Optional[str] = None,
    bank: Optional[BankLeg] = None,
    pan: Optional[str] = None,
    mobile: Optional[str] = None,
    email: Optional[str] = None,
    reason: Optional[str] = None,
    reason_code: Optional[str] = None,
) -> MfcFtOrder:
    """Build, record, and submit one financial transaction.

    Returns the persisted order with MFC's ``reqId``. The investor still has to
    consent — nothing is executed until :func:`send_otp` and :func:`confirm_otp`
    complete, and even then the RTA's verdict comes from
    :func:`refresh_status`.
    """
    _validate(kind, leg)

    amc_code = resolve_amc_code(amc) or resolve_amc_code(leg.scheme_name)
    if not amc_code:
        raise MfcFtError(
            "We could not work out which fund house this scheme belongs to. "
            "Please supply the AMC code.",
            stage="amc",
        )

    effective_pan, out_mobile, out_email = await _resolve_investor(
        db, user_id, pan=pan, mobile=mobile, email=email
    )

    client_ref_no = _new_client_ref_no()
    extra: dict[str, Any] = {}
    if kind in (MfcFtKind.SIP_PAUSE, MfcFtKind.SIP_CANCEL):
        # The registrar records why a SIP stopped; it shows on the investor's
        # statement, so an empty reason is a real (if permitted) choice.
        extra = {"reason": _s(reason), "code": _s(reason_code), "source": "MFC"}
    elif kind in (MfcFtKind.PURCHASE, MfcFtKind.ADDITIONAL) and bank:
        extra = {
            "bank": _bank_block(bank),
            "tpvFlag": "Y",
            "payInMode": "web",
            "instNo": "",
            "omUMRN": "",
            "mandateRefId": "",
            "payInRefno": "",
            "payInBankName": _s(bank.name),
            "payInBankAcno": _s(bank.account_no),
        }

    payload = _envelope(
        kind=kind,
        client_ref_no=client_ref_no,
        pan=effective_pan,
        mobile=out_mobile,
        email=out_email,
        amc=amc_code,
        scheme_options=_scheme_options(kind, leg, bank),
        extra_block=extra,
    )

    order = MfcFtOrder(
        user_id=user_id,
        kind=kind.value,
        status=MfcFtStatus.DRAFT.value,
        client_ref_no=client_ref_no,
        amc=amc_code,
        folio=leg.folio,
        isin=(leg.isin or "").upper() or None,
        to_isin=(leg.to_isin or "").upper() or None,
        scheme_name=leg.scheme_name,
        amount=leg.amount,
        units=leg.units,
        all_units=leg.all_units,
        frequency=resolve_frequency(leg.frequency),
        start_date=leg.start_date,
        end_date=leg.end_date,
        installments=leg.installments,
        user_trxn_no=leg.user_trxn_no,
        pan=effective_pan,
        otp_channel="M" if out_mobile else "E",
        otp_destination=out_mobile or out_email,
        request_payload=payload,
    )
    db.add(order)
    # Committed BEFORE the call. An order MFC accepts but whose response we lose
    # must still be one we can name to them — see the module docstring.
    await db.commit()
    await db.refresh(order)

    try:
        response = await mfc_ft_client.submit_transaction(kind, payload)
    except MfcApiError as exc:
        await _fail(db, order, f"{exc.stage or kind.value}: {exc.short_reason}")
        logger.warning("MFC FT %s submit failed: %s", kind.value, exc.short_reason)
        raise _api_error_to_ft(exc) from exc
    except MfcCryptoError as exc:
        await _fail(db, order, f"crypto: {exc}")
        raise MfcFtError(
            f"MF Central integration is misconfigured: {exc}", stage="crypto"
        ) from exc

    order.response_payload = response
    rejections = mfc_ft_client.extract_errors(response)
    if rejections:
        # A 200 carrying per-scheme errors is a documented outcome. Reading only
        # the status code would report a rejected order as placed.
        first = rejections[0]
        order.status = MfcFtStatus.REJECTED.value
        order.error = str(first.get("message") or first.get("responseMessage") or "")[
            :2000
        ]
        order.rta_status = str(first.get("transactionStatus") or "")[:120] or None
        order.completed_at = datetime.now(timezone.utc)
        await db.commit()
        raise MfcFtError(
            order.error or "MF Central rejected this order.", stage="rejected"
        )

    order.req_id = str(response.get("reqId") or "").strip() or None
    order.status = MfcFtStatus.SUBMITTED.value
    await db.commit()
    await db.refresh(order)
    return order


# --------------------------------------------------------------------------- step 2/3


async def send_otp(db: AsyncSession, order: MfcFtOrder) -> MfcFtOrder:
    """Ask MFC to send the investor the transaction OTP."""
    _require_live(order)
    if not order.req_id:
        raise MfcFtError("This order never reached MF Central.", stage="otp")

    try:
        response = await mfc_ft_client.generate_otp(
            req_id=order.req_id, client_ref_no=order.client_ref_no
        )
    except MfcApiError as exc:
        await _fail(db, order, f"generateOTP: {exc.short_reason}")
        raise _api_error_to_ft(exc) from exc

    order.otp_ref = str(response.get("otpRef") or "").strip() or None
    order.status = MfcFtStatus.OTP_SENT.value
    await db.commit()
    await db.refresh(order)
    return order


async def confirm_otp(db: AsyncSession, order: MfcFtOrder, otp: str) -> MfcFtOrder:
    """Relay the investor's OTP. This is the point of no return.

    A wrong code is NOT terminal — MFC rejects it and the same otpRef can be
    retried, so the order stays at ``otp_sent`` rather than failing. Marking it
    failed would strand an order over a typo.
    """
    _require_live(order)
    if not order.otp_ref or not order.req_id:
        raise MfcFtError(
            "Ask for the OTP before confirming it.", stage="investorconsent"
        )
    code = (otp or "").strip()
    if not code:
        raise MfcFtError("Enter the OTP MF Central sent you.", stage="investorconsent")

    try:
        await mfc_ft_client.submit_consent(
            req_id=order.req_id,
            otp_ref=order.otp_ref,
            client_ref_no=order.client_ref_no,
            entered_otp=code,
        )
    except MfcApiError as exc:
        if exc.status_code in (400, 422):
            raise MfcFtError(
                exc.short_reason
                or "That OTP was not accepted. Check the code and try again.",
                stage="investorconsent",
            ) from exc
        await _fail(db, order, f"investorconsent: {exc.short_reason}")
        raise _api_error_to_ft(exc) from exc

    order.status = MfcFtStatus.CONSENTED.value
    order.consented_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(order)
    return order


# --------------------------------------------------------------------------- step 4

# The RTA's own words -> our status. Matched case-insensitively on a substring
# because the exact phrasing differs between CAMS and KFintech ("Transaction
# rejected" vs "REJECTED"), and neither publishes a closed list.
_RTA_STATUS_MAP: tuple[tuple[str, MfcFtStatus], ...] = (
    ("reject", MfcFtStatus.REJECTED),
    ("fail", MfcFtStatus.FAILED),
    ("cancel", MfcFtStatus.REJECTED),
    ("success", MfcFtStatus.SUCCESS),
    ("complet", MfcFtStatus.SUCCESS),
    ("allot", MfcFtStatus.SUCCESS),
    ("accept", MfcFtStatus.PROCESSING),
    ("pending", MfcFtStatus.PROCESSING),
    ("process", MfcFtStatus.PROCESSING),
)


async def refresh_status(db: AsyncSession, order: MfcFtOrder) -> MfcFtOrder:
    """Poll the RTA's verdict. Unlimited by MFC's documentation.

    Terminal orders short-circuit rather than erroring: a client polling one is
    asking a reasonable question and should get the answer, not a 400.
    """
    if order.status in MFC_FT_TERMINAL:
        return order
    if not order.req_id:
        raise MfcFtError("This order never reached MF Central.", stage="status")

    try:
        payload = await mfc_ft_client.get_transaction_status(
            req_id=order.req_id, client_ref_no=order.client_ref_no
        )
    except MfcApiError as exc:
        # A failed poll says nothing about the ORDER — only that we could not
        # ask. Leaving the status untouched is the honest outcome.
        raise _api_error_to_ft(exc) from exc

    order.status_payload = payload
    rta_status, message, user_trxn_no = _read_status(payload)
    if rta_status:
        order.rta_status = rta_status[:120]
        order.status = _map_rta_status(rta_status).value
    if message:
        order.error = message[:2000]
    if user_trxn_no:
        order.user_trxn_no = user_trxn_no[:64]
    if order.status in MFC_FT_TERMINAL and order.completed_at is None:
        order.completed_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(order)
    return order


def _map_rta_status(text: str) -> MfcFtStatus:
    lowered = text.lower()
    for needle, status in _RTA_STATUS_MAP:
        if needle in lowered:
            return status
    return MfcFtStatus.PROCESSING


def _read_status(
    payload: dict[str, Any],
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Pull (status, message, userTrxnNo) out of whichever shape MFC returned.

    CAMS nests the per-scheme detail under ``errors`` even for successes;
    KFintech uses ``success``/``error``. Rather than branch on registrar, walk
    the first list of dicts we find — the fields inside are consistently named.
    """
    for key in ("errors", "error", "success", "data", "transactions"):
        rows = payload.get(key)
        if isinstance(rows, list) and rows and isinstance(rows[0], dict):
            row = rows[0]
            return (
                _first_str(row, "transactionStatus", "status", "responseMessage"),
                _first_str(row, "message", "techMessage", "responseMessage"),
                _first_str(row, "userTrxnNo", "userTrxNo"),
            )
    return (
        _first_str(payload, "transactionStatus", "status"),
        _first_str(payload, "message", "errorMessage"),
        _first_str(payload, "userTrxnNo", "userTrxNo"),
    )


def _first_str(row: dict[str, Any], *keys: str) -> Optional[str]:
    for key in keys:
        value = row.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


# --------------------------------------------------------------------------- payment


async def update_payment(
    db: AsyncSession,
    order: MfcFtOrder,
    *,
    status: str = "SUCCESS",
    bank_code: Optional[str] = None,
    umrn: Optional[str] = None,
    mandate_ref_id: Optional[str] = None,
    error_description: Optional[str] = None,
) -> MfcFtOrder:
    """Tell MFC the money moved, for purchases and SIP registrations.

    Without this the RTA holds the order unfunded — it is part of placing the
    order, not bookkeeping after it.
    """
    if order.kind not in (MfcFtKind.PURCHASE.value, MfcFtKind.ADDITIONAL.value):
        raise MfcFtError(
            "Only purchases and SIP registrations carry a payment update.",
            stage="payment",
        )
    if not order.req_id:
        raise MfcFtError("This order never reached MF Central.", stage="payment")

    payload = {
        "reqId": order.req_id,
        "pan": order.pan or "",
        "mobile": order.otp_destination if order.otp_channel == "M" else "",
        "email": order.otp_destination if order.otp_channel == "E" else "",
        "pekrn": "",
        "data": [
            {
                "amc": order.amc or "",
                "paymentDetails": [
                    {
                        "folio": order.folio or "",
                        "isin": order.isin or "",
                        "userTrxnNo": order.user_trxn_no or "",
                        "instNo": "",
                        "instDateTime": "",
                        "omUMRN": umrn or "",
                        "mandateRefId": mandate_ref_id or "",
                        "status": status,
                        "errorDescription": error_description or "",
                        "paymentGatewayRequest": "",
                        "bankCode": bank_code or "",
                        "otmType": "CAMSOTM",
                        "payInMech": "OTM",
                    }
                ],
            }
        ],
    }

    try:
        response = await mfc_ft_client.update_payment(payload)
    except MfcApiError as exc:
        raise _api_error_to_ft(exc) from exc

    order.status_payload = {**(order.status_payload or {}), "payment": response}
    await db.commit()
    await db.refresh(order)
    return order


# --------------------------------------------------------------------------- pause pre-flight


async def validate_pause_cancel(
    db: AsyncSession,
    user_id: uuid.UUID,
    *,
    kind: MfcFtKind,
    leg: SchemeLeg,
    amc: Optional[str] = None,
    pan: Optional[str] = None,
    mobile: Optional[str] = None,
    email: Optional[str] = None,
) -> dict[str, Any]:
    """Dry-run a pause/cancel. Writes nothing — it only asks MFC if it would work.

    Worth its own call because skipping it turns a wrong ``userTrxnNo`` into a
    rejection AFTER the investor has been sent an OTP.
    """
    _validate(kind, leg)
    amc_code = resolve_amc_code(amc) or resolve_amc_code(leg.scheme_name)
    if not amc_code:
        raise MfcFtError("Please supply the AMC code.", stage="amc")

    effective_pan, out_mobile, out_email = await _resolve_investor(
        db, user_id, pan=pan, mobile=mobile, email=email
    )
    payload = {
        "clientRefNo": _new_client_ref_no(),
        "email": out_email or "",
        "mobile": out_mobile or "",
        "pan": effective_pan,
        "pekrn": "",
        "amc": amc_code,
        "data": [
            {
                "code": "",
                "reason": "",
                "schemeOptions": _scheme_options(kind, leg, None),
            }
        ],
    }
    try:
        return await mfc_ft_client.validate_sip_pause_cancel(payload)
    except MfcApiError as exc:
        raise _api_error_to_ft(exc) from exc


# --------------------------------------------------------------------------- shared


def _require_live(order: MfcFtOrder) -> None:
    if order.status in MFC_FT_TERMINAL:
        raise MfcFtError(
            f"This order is already {order.status} and cannot be advanced.",
            stage="state",
        )


async def _fail(db: AsyncSession, order: MfcFtOrder, reason: str) -> None:
    order.status = MfcFtStatus.FAILED.value
    order.error = reason[:2000]
    order.completed_at = datetime.now(timezone.utc)
    await db.commit()


async def get_order(
    db: AsyncSession, user_id: uuid.UUID, order_id: uuid.UUID
) -> MfcFtOrder:
    """One of the caller's own orders. Scoped to the user, always."""
    order = (
        await db.execute(
            select(MfcFtOrder).where(
                MfcFtOrder.id == order_id, MfcFtOrder.user_id == user_id
            )
        )
    ).scalar_one_or_none()
    if order is None:
        raise MfcFtError("Order not found.", stage="lookup")
    return order


async def list_orders(
    db: AsyncSession, user_id: uuid.UUID, *, limit: int = 50
) -> list[MfcFtOrder]:
    return list(
        (
            await db.execute(
                select(MfcFtOrder)
                .where(MfcFtOrder.user_id == user_id)
                .order_by(MfcFtOrder.created_at.desc())
                .limit(max(1, min(limit, 200)))
            )
        )
        .scalars()
        .all()
    )
