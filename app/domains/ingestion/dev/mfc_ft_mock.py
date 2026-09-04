"""DEV-ONLY — MF Central's Financial Transaction endpoints, mocked.

The outbound counterpart to :mod:`mfc_mock`, which covers CAS. Split into its
own module because it is a different product with different commercials, and
because the two mocks fail differently: a CAS mock only has to hand back a
document, while this one has to model an ORDER — something that exists on the
registrar's side, changes state over time, and can be rejected inside a 200.

It reuses the CAS mock's envelope helpers verbatim, so a change to the
encrypt/sign scheme cannot leave the two halves disagreeing.

Three behaviours are modelled on purpose, because each is a way our client
could be wrong in a manner UAT would only reveal late:

* ``investorconsent`` answers **202 with an empty body**. A client that treats
  an empty response as failure fails every correctly consented order.
* ``getFtTransactionStatus`` reports "under process" on the first poll and
  settles on the second. A client that assumes the first poll is final reports
  a pending order as complete.
* an amount ending in ``13`` is **rejected inside an HTTP 200**, in CAMS's
  ``errors: [...]`` shape. A client that reads only the status code reports a
  rejected order as placed.

Not a conformance test. It implements MFC's documentation; where the live
service differs, this will agree with us and the live one will not.
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from app.domains.ingestion.dev.mfc_mock import _check_auth, _counter, _unwrap, _wrap

router = APIRouter(tags=["MF Central FT (mock)"], include_in_schema=False)

# reqId -> the order we were handed, so the OTP, consent and status legs answer
# consistently about the same order rather than from a fixed script.
_orders: dict[str, dict[str, Any]] = {}

# The transaction families. Anything else on this path is a 404, so a typo in
# our endpoint map surfaces here rather than as a confusing success.
FT_ENDPOINTS: frozenset[str] = frozenset(
    {
        "distNewPurchase",
        "distAdditionalPurchase",
        "distRedeem",
        "distSwitch",
        "distRegisterStp",
        "distRegisterSwp",
        "submitSipPauseCancel",
    }
)

# Amounts ending in these two digits are rejected, so the
# 200-carrying-per-scheme-errors path can be walked deliberately.
REJECT_AMOUNT_SUFFIX = "13"


def expected_otp(pan: str) -> str:
    """UAT OTP rule: ``00`` + the last four digits of the PAN.

    Borrowed from MFC's own CAS convention because their FT docs state no rule.
    A fixed code would let an OTP that was never actually delivered pass, which
    is the one thing this leg exists to prove.
    """
    digits = "".join(c for c in (pan or "") if c.isdigit())
    return f"00{digits[-4:]}" if len(digits) >= 4 else "000000"


def _first_option(order: dict[str, Any]) -> dict[str, Any]:
    block = (order.get("data") or [{}])[0]
    options = block.get("schemeOptions") or [{}]
    return options[0] if isinstance(options[0], dict) else {}


def _rejection(reason: str, order: dict[str, Any]) -> dict[str, Any]:
    """A per-scheme rejection inside a 200 — the shape CAMS actually returns."""
    block = (order.get("data") or [{}])[0]
    option = _first_option(order)
    return {
        "reqId": order.get("_reqId", ""),
        "pan": order.get("pan", ""),
        "pekrn": "",
        "email": order.get("email", ""),
        "mobile": order.get("mobile", ""),
        "amc": block.get("amc", ""),
        "rtaCode": "Cams",
        "errors": [
            {
                "itemNo": "",
                "isin": option.get("isin") or option.get("fromIsin") or "",
                "code": "400",
                "amc": block.get("amc", ""),
                "amcName": "",
                "schemeName": "",
                "schemeCode": "",
                "schemeType": "NONLIQUID",
                "userTrxnNo": "",
                "transactionType": option.get("trxnType", ""),
                "amount": option.get("amount", ""),
                "nav": "",
                "navDate": "",
                "message": reason,
                "techMessage": "",
                "entryDateTime": "",
                "transactionStatus": "Transaction rejected",
            }
        ],
    }


def _require_order(req_id: str) -> dict[str, Any]:
    order = _orders.get(str(req_id))
    if order is None:
        raise HTTPException(
            status_code=400, detail={"code": "error", "message": "Unknown reqId"}
        )
    return order


@router.post("/api/client/V1/generateOTP")
async def generate_otp(
    request: Request,
    authorization: Optional[str] = Header(default=None),
    clientid: Optional[str] = Header(default=None, alias="ClientId"),
):
    _check_auth(authorization, clientid)
    payload = _unwrap(await request.json())
    req_id = str(payload.get("reqId") or "")
    order = _require_order(req_id)
    order["_otpRef"] = f"mock-ft-otp-{req_id}"
    return _wrap(
        {
            "reqId": int(req_id) if req_id.isdigit() else req_id,
            "otpRef": order["_otpRef"],
            "userSubjectReference": "",
            "clientRefNo": order.get("clientRefNo", ""),
        }
    )


# MFC's docs spell this endpoint lower-case in the samples and camel-case in the
# flow diagrams, and both reach the same handler on their side. Registering both
# means a casing slip in our client fails loudly here rather than as a 404 in UAT.
@router.post("/api/client/V1/investorconsent")
@router.post("/api/client/V1/investorConsent")
async def investor_consent(
    request: Request,
    authorization: Optional[str] = Header(default=None),
    clientid: Optional[str] = Header(default=None, alias="ClientId"),
):
    _check_auth(authorization, clientid)
    payload = _unwrap(await request.json())
    order = _require_order(str(payload.get("reqId") or ""))

    if payload.get("otpRef") != order.get("_otpRef"):
        raise HTTPException(
            status_code=400,
            detail={"code": "error", "message": "OTP reference does not match"},
        )
    if str(payload.get("enteredOtp") or "") != expected_otp(
        str(order.get("pan") or "")
    ):
        raise HTTPException(
            status_code=400,
            detail={"code": "error", "message": "OTP validation failed"},
        )

    order["_consented"] = True
    # 202 with an EMPTY body, exactly as MFC documents.
    return JSONResponse(status_code=202, content={})


@router.post("/api/client/V1/getFtTransactionStatus")
async def transaction_status(
    request: Request,
    authorization: Optional[str] = Header(default=None),
    clientid: Optional[str] = Header(default=None, alias="ClientId"),
):
    _check_auth(authorization, clientid)
    payload = _unwrap(await request.json())
    req_id = str(payload.get("reqId") or "")
    order = _require_order(req_id)

    block = (order.get("data") or [{}])[0]
    option = _first_option(order)
    order["_polls"] = int(order.get("_polls") or 0) + 1

    if not order.get("_consented"):
        # An unconsented order sits pending indefinitely, as the real one does:
        # consent is what releases it to the registrar.
        state, message = "Pending for consent", "Awaiting investor consent"
    elif order["_polls"] < 2:
        state, message = "Transaction under process", "Sent to the RTA"
    else:
        state, message = "Transaction Successful", "Order accepted by the RTA"

    return _wrap(
        {
            "reqId": req_id,
            "clientRefNo": order.get("clientRefNo", ""),
            "pan": order.get("pan", ""),
            "amc": block.get("amc", ""),
            "rtaCode": "Cams",
            "errors": [
                {
                    "itemNo": "",
                    "isin": option.get("isin") or option.get("fromIsin") or "",
                    "amc": block.get("amc", ""),
                    "schemeName": "",
                    "userTrxnNo": f"MOCK{req_id}",
                    "transactionType": option.get("trxnType", ""),
                    "amount": option.get("amount", ""),
                    "message": message,
                    "techMessage": "",
                    "entryDateTime": "",
                    "transactionStatus": state,
                }
            ],
        }
    )


@router.post("/api/client/V1/ftPaymentUpdate")
async def payment_update(
    request: Request,
    authorization: Optional[str] = Header(default=None),
    clientid: Optional[str] = Header(default=None, alias="ClientId"),
):
    _check_auth(authorization, clientid)
    payload = _unwrap(await request.json())
    details = ((payload.get("data") or [{}])[0].get("paymentDetails") or [{}])[0]
    return _wrap(
        {
            "reqId": payload.get("reqId", ""),
            "status": details.get("status", "SUCCESS"),
            "message": "Payment status recorded",
        }
    )


@router.post("/api/client/V1/validateSipPauseCancel")
async def validate_pause_cancel(
    request: Request,
    authorization: Optional[str] = Header(default=None),
    clientid: Optional[str] = Header(default=None, alias="ClientId"),
):
    _check_auth(authorization, clientid)
    payload = _unwrap(await request.json())
    option = ((payload.get("data") or [{}])[0].get("schemeOptions") or [{}])[0]

    if not option.get("userTrxnNo"):
        return _wrap(
            {
                "errors": [
                    {
                        "message": "No running SIP found for this folio",
                        "transactionStatus": "Validation failed",
                    }
                ]
            }
        )
    return _wrap(
        {
            "clientRefNo": payload.get("clientRefNo", ""),
            "success": [
                {
                    "userTrxnNo": option.get("userTrxnNo"),
                    "isin": option.get("isin", ""),
                    "transactionStatus": "Eligible",
                    "message": "This SIP can be paused or cancelled",
                }
            ],
        }
    )


@router.post("/api/client/V1/{endpoint}")
async def place_transaction(
    endpoint: str,
    request: Request,
    authorization: Optional[str] = Header(default=None),
    clientid: Optional[str] = Header(default=None, alias="ClientId"),
):
    """All eight transaction families.

    One handler because MFC's envelope is one shape — the family changes only
    which scheme fields are populated. Declared LAST so the named endpoints
    above keep their own handlers; FastAPI matches in declaration order.
    """
    _check_auth(authorization, clientid)
    if endpoint not in FT_ENDPOINTS:
        raise HTTPException(status_code=404, detail="Unknown MF Central endpoint")

    payload = _unwrap(await request.json())
    blocks = payload.get("data") or []
    if not blocks:
        raise HTTPException(
            status_code=422, detail={"code": "error", "message": "data[] is required"}
        )
    block = blocks[0]
    options = block.get("schemeOptions") or []
    if not options:
        raise HTTPException(
            status_code=422,
            detail={"code": "error", "message": "schemeOptions[] is required"},
        )

    pan = str(payload.get("pan") or "")
    if len(pan) != 10:
        raise HTTPException(
            status_code=400, detail={"code": "error", "message": "PAN is not valid"}
        )
    if bool(payload.get("mobile")) == bool(payload.get("email")):
        raise HTTPException(
            status_code=400,
            detail={"code": "error", "message": "Pass exactly one of mobile or email"},
        )

    # The otpSentTo / otpMobile / otpEmail agreement. MFC rejects a mismatch
    # with a 400 that names no field, so asserting it here means our envelope
    # builder is what gets corrected rather than a call site.
    sent_to = block.get("otpSentTo")
    if sent_to == "M" and not block.get("otpMobile"):
        raise HTTPException(
            status_code=400,
            detail={"code": "error", "message": "otpSentTo=M but otpMobile is empty"},
        )
    if sent_to == "E" and not block.get("otpEmail"):
        raise HTTPException(
            status_code=400,
            detail={"code": "error", "message": "otpSentTo=E but otpEmail is empty"},
        )
    if not block.get("amc"):
        raise HTTPException(
            status_code=400, detail={"code": "error", "message": "amc is required"}
        )

    _counter["n"] += 1
    req_id = str(_counter["n"])
    order = dict(payload)
    order["_reqId"] = req_id
    order["_endpoint"] = endpoint
    _orders[req_id] = order

    amount = str(options[0].get("amount") or "")
    if amount.endswith(REJECT_AMOUNT_SUFFIX):
        return _wrap(
            _rejection("Scheme not available for transaction at this time", order)
        )

    return _wrap({"clientRefNo": payload.get("clientRefNo", ""), "reqId": req_id})
