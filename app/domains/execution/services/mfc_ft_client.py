"""MF Central Financial Transaction endpoints, over the shared MFC envelope.

The FT APIs and the CAS APIs are the same protocol — same OAuth token, same
AES + detached-JWS envelope, same error table — so this deliberately owns no
crypto and no HTTP. It is the endpoint map and the four-call chain, layered on
:class:`~app.domains.ingestion.services.mfc_client.MfcClient`, which means a
change to the envelope is still made in exactly one place.

The chain, identical for all eight transaction families:

    <family endpoint>   -> reqId                         "we have an order"
    generateOTP         -> otpRef                        "MFC texted the investor"
    investorconsent     -> 202 Accepted, no body         "the code was right"
    getFtTransactionStatus                               "what did the RTA do"

Two of those are worth stating plainly because they are unlike the rest of our
vendor calls:

* ``investorconsent`` answers **202 with an empty body** on success. Treating
  an empty response as a failure would fail every correctly consented order.
* ``getFtTransactionStatus`` is documented as unlimited — MFC explicitly places
  no cap on polling. It is the only honest source of an order's outcome, since
  every earlier step reports only that MFC accepted the request.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from app.domains.execution.models.mfc_ft_order import MfcFtKind
from app.domains.ingestion.services.mfc_client import MfcApiError, get_mfc_client

logger = logging.getLogger(__name__)

# Transaction family -> MFC endpoint. The SIP pause and cancel families share a
# submit endpoint and differ only by the per-scheme trxnType (PSIP / CSIP).
FT_ENDPOINTS: dict[MfcFtKind, str] = {
    MfcFtKind.PURCHASE: "/api/client/V1/distNewPurchase",
    MfcFtKind.ADDITIONAL: "/api/client/V1/distAdditionalPurchase",
    MfcFtKind.REDEEM: "/api/client/V1/distRedeem",
    MfcFtKind.SWITCH: "/api/client/V1/distSwitch",
    MfcFtKind.STP: "/api/client/V1/distRegisterStp",
    MfcFtKind.SWP: "/api/client/V1/distRegisterSwp",
    MfcFtKind.SIP_PAUSE: "/api/client/V1/submitSipPauseCancel",
    MfcFtKind.SIP_CANCEL: "/api/client/V1/submitSipPauseCancel",
}

# The top-level ``otherAPI`` code MFC uses to route the envelope internally.
FT_OTHER_API: dict[MfcFtKind, str] = {
    MfcFtKind.PURCHASE: "NP",
    MfcFtKind.ADDITIONAL: "AP",
    MfcFtKind.REDEEM: "R",
    MfcFtKind.SWITCH: "S",
    MfcFtKind.STP: "ST",
    MfcFtKind.SWP: "SW",
    MfcFtKind.SIP_PAUSE: "SPC",
    MfcFtKind.SIP_CANCEL: "SPC",
}

_OTP_PATH = "/api/client/V1/generateOTP"
_CONSENT_PATH = "/api/client/V1/investorconsent"
_STATUS_PATH = "/api/client/V1/getFtTransactionStatus"
_PAYMENT_PATH = "/api/client/V1/ftPaymentUpdate"
_VALIDATE_PAUSE_PATH = "/api/client/V1/validateSipPauseCancel"


async def submit_transaction(
    kind: MfcFtKind, payload: dict[str, Any]
) -> dict[str, Any]:
    """POST one built FT payload to its family's endpoint.

    Returns MFC's ``{clientRefNo, reqId}``. A 400 here is a business rejection
    (bad folio, scheme not transactable, contact tagged to a different PAN) and
    must not be retried: MFC may already hold the order.
    """
    endpoint = FT_ENDPOINTS[kind]
    result = await get_mfc_client().call_signed(endpoint, payload, stage=kind.value)
    if not isinstance(result, dict):
        raise MfcApiError(
            "MF Central returned an unexpected order response.",
            body=result,
            stage=kind.value,
        )
    return result


async def generate_otp(*, req_id: str, client_ref_no: str) -> dict[str, Any]:
    """Ask MFC to send the investor the transaction OTP. Returns ``otpRef``."""
    result = await get_mfc_client().call_signed(
        _OTP_PATH,
        {"clientRefNo": client_ref_no, "reqId": str(req_id)},
        stage="generateOTP",
    )
    if not isinstance(result, dict) or not result.get("otpRef"):
        raise MfcApiError(
            "MF Central did not return an OTP reference.",
            body=result,
            stage="generateOTP",
        )
    return result


async def submit_consent(
    *,
    req_id: str,
    otp_ref: str,
    client_ref_no: str,
    entered_otp: str,
    user_subject_reference: str = "",
) -> dict[str, Any]:
    """Relay the investor's OTP. Success is **202 with an empty body**.

    The empty dict that comes back is the success signal, not a missing value —
    ``MfcClient._unwrap`` returns ``{}`` for a 202 precisely so this reads as an
    outcome rather than an anomaly.
    """
    return await get_mfc_client().call_signed(
        _CONSENT_PATH,
        {
            "reqId": _as_number_if_possible(req_id),
            "otpRef": otp_ref,
            "userSubjectReference": user_subject_reference,
            "clientRefNo": client_ref_no,
            "enteredOtp": entered_otp,
        },
        stage="investorconsent",
    )


async def get_transaction_status(*, req_id: str, client_ref_no: str) -> dict[str, Any]:
    """Poll the RTA's own verdict. Unlimited by MFC's documentation.

    This is the only call that reports what actually happened: everything
    earlier reports that MFC accepted a request, which is not the same thing as
    an AMC having allotted units.
    """
    result = await get_mfc_client().call_signed(
        _STATUS_PATH,
        {
            "reqId": _as_number_if_possible(req_id),
            "clientRefNo": client_ref_no,
        },
        stage="getFtTransactionStatus",
    )
    return result if isinstance(result, dict) else {"raw": result}


async def update_payment(payload: dict[str, Any]) -> dict[str, Any]:
    """Tell MFC the money moved, for purchases and SIP registrations.

    Without this the RTA holds the order unfunded; it is not an optional
    bookkeeping step.
    """
    result = await get_mfc_client().call_signed(
        _PAYMENT_PATH, payload, stage="ftPaymentUpdate"
    )
    return result if isinstance(result, dict) else {"raw": result}


async def validate_sip_pause_cancel(payload: dict[str, Any]) -> dict[str, Any]:
    """Dry-run a pause/cancel against a live SIP before submitting it.

    The only FT family with a pre-flight step: MFC checks the SIP is real,
    still running, and pausable for the requested number of instalments. Skipping
    it turns a wrong ``userTrxnNo`` into a rejection after the investor has
    already been asked for an OTP.
    """
    result = await get_mfc_client().call_signed(
        _VALIDATE_PAUSE_PATH, payload, stage="validateSipPauseCancel"
    )
    return result if isinstance(result, dict) else {"raw": result}


def _as_number_if_possible(req_id: str) -> Any:
    """MFC's samples send ``reqId`` as a bare number on the consent and status
    calls but as a string elsewhere, and their hyphenated ids cannot be numbers
    at all. Send whichever the value actually is."""
    text = str(req_id).strip()
    return int(text) if text.isdigit() else text


def extract_errors(payload: Optional[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per-scheme rejections out of an FT response, whichever shape it arrived in.

    KFintech answers with ``error: [...]`` and CAMS with ``errors: [...]``, and
    both can appear alongside an HTTP 200 — a partially accepted batch is a
    documented outcome, not an exception. Reading only the status code would
    report a rejected order as placed.
    """
    if not isinstance(payload, dict):
        return []
    rows: list[dict[str, Any]] = []
    for key in ("errors", "error"):
        value = payload.get(key)
        if isinstance(value, list):
            rows.extend(r for r in value if isinstance(r, dict))
    return rows
