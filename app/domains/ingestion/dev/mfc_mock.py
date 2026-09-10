"""DEV-ONLY — MF Central, mounted inside our own app at ``/mfc-mock``.

MFC issues UAT credentials only after an LOI is signed, so the integration would
otherwise be untestable until commercial paperwork completes. This speaks their
documented protocol — same endpoints, same encrypt/sign envelope, same response
shapes — against an RSA pair minted on this machine, so the whole flow can be
walked today: token cache, envelope, detached JWS, adapter, ingest, and the
frontend screens end to end.

It is mounted rather than run separately so ``uvicorn main:app --reload`` is the
only process anyone needs. The backend talks to it over loopback like any other
vendor, which keeps the real client, the real HTTP, and the real retry logic in
the path — a stubbed transport would test none of that.

What it is NOT: a validator of MFC's real behaviour. It implements their guide
and their Postman capture. Where the live service differs (and somewhere it
will), this will happily agree with us and the live one will not.

Gated by :func:`Settings.mfc_mock_enabled`, which cannot be true when real
credentials are configured or ``DEPLOY_ENV=production``.
"""

from __future__ import annotations

import base64
import json
import re
import zlib
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

from app.core.config import Settings
from app.domains.ingestion.services import mfc_crypto

# Fixed, published-in-the-guide values. Not secrets — the mock only ever holds
# sample data, and pinning them keeps a regenerated key file from invalidating
# an .env someone already wrote by hand.
MOCK_CLIENT_ID = "prozpr-mock-client"
MOCK_CLIENT_SECRET = "prozpr-mock-secret"
MOCK_USERNAME = "prozpr-mock-user"
MOCK_PASSWORD = "prozpr-mock-password"
MOCK_ENCRYPTION_KEY = "prozpr-mock-encryption-key"
MOCK_IV = "1234567890123456"
# Exactly 32 chars — MFC's own UAT value from the integration guide.
MOCK_URL_KEY = "odLZvcdVAhq+Re8UstN8u928xRQTE6ym"

_BACKEND_ROOT = Path(__file__).resolve().parents[4]
KEY_FILE = _BACKEND_ROOT / ".mfc-mock-keys.json"

# A 1x1 PNG. Real MFC encodes an opaque token in a real QR; all that matters for
# wiring is that the bytes survive a download and re-upload unchanged and are
# long enough to clear our size guards.
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

_keys_cache: Optional[tuple[str, str]] = None


def keys() -> tuple[str, str]:
    """The mock's RSA pair, minted on first use and cached on disk.

    One pair stands in for MFC's two-key exchange: the mock verifies OUR
    signature with the public half and signs its own responses with the private
    half. Persisting matters — regenerating per process would invalidate an
    ``.env`` written from a previous run, and would make ``--reload`` change the
    keys mid-session.
    """
    global _keys_cache
    if _keys_cache is not None:
        return _keys_cache

    if KEY_FILE.exists():
        data = json.loads(KEY_FILE.read_text(encoding="utf-8"))
        _keys_cache = (data["private_key"], data["public_key"])
        return _keys_cache

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_pem = (
        key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    KEY_FILE.write_text(
        json.dumps({"private_key": private_pem, "public_key": public_pem}, indent=2),
        encoding="utf-8",
    )
    _keys_cache = (private_pem, public_pem)
    return _keys_cache


# --------------------------------------------------------------------------- payloads

SUMMARY_SAMPLE: dict[str, Any] = {
    "pan": "AGVPN4690G",
    "mobile": "9550755111",
    "email": "",
    "pekrn": "",
    "data": [
        {
            "summary": [
                {
                    "amc": "108",
                    "amcName": "UTI MUTUAL FUND",
                    "currentMktValue": 16942.16,
                    "costValue": 14999.25,
                    "gainLoss": 1942.91,
                    "gainLossPercentage": 13,
                    "isDemat": "N",
                }
            ],
            "schemes": [
                {
                    "amc": "108",
                    "amcName": "UTI MUTUAL FUND",
                    "folio": "599366447236",
                    "investorName": "Testing XYZ",
                    "age": 39,
                    "mobile": "9550755111",
                    "email": "testing.xyz@example.com",
                    "taxStatus": "01",
                    "modeOfHolding": "SINGLE",
                    "schemeCode": "EQGP",
                    "schemeName": "UTI Flexi Cap Fund - Regular Plan - Growth",
                    "schemeOption": "GROWTH",
                    "assetType": "EQUITY",
                    "schemeType": "EQUITY FUND",
                    "nav": 321.7826,
                    "navDate": "01-Jan-2026",
                    "closingBalance": 16.521,
                    "availableUnits": 16.521,
                    "lienEligibleUnits": 16.521,
                    "currentMktValue": 5316.17,
                    "costValue": 4999.75,
                    "gainLoss": 316.42,
                    "gainLossPercentage": 6.3,
                    "isin": "INF789F01513",
                    "brokerCode": "ARN-154065",
                    "brokerName": "A Distributor",
                    "isDemat": "N",
                    "purAllow": "Y",
                    "redAllow": "Y",
                    "swtAllow": "Y",
                    "sipAllow": "Y",
                    "stpAllow": "N",
                    "swpAllow": "N",
                    "planMode": "REGULAR",
                    "rtaName": "KFIN",
                    "nomineeStatus": "Y",
                    "kycStatus": "02",
                    "bank": {
                        "accountNo": "068001510540",
                        "accountType": "SAVING",
                        "name": "ICICI BANK LIMITED",
                        "branch": "",
                        "city": "HYDERABAD",
                        "pincode": "",
                        "micr": "",
                        "ifsc": "ICIC0000680",
                        "neftIfsc": "ICIC0000680",
                    },
                }
            ],
        }
    ],
    "errorMessage": "",
    "portfolio": [
        {
            "currentMktValue": "16942.16",
            "costValue": "14999.25",
            "gainLoss": "1942.91",
            "gainLossPercentage": "12.95",
            "isDemat": "N",
        },
        {
            "currentMktValue": "0.00",
            "costValue": "0.00",
            "gainLoss": "0.00",
            "gainLossPercentage": "0.00",
            "isDemat": "Y",
        },
    ],
    "investorDetails": {},
    "statementHoldingFilter": None,
}


def _txn(
    scheme: str,
    isin: str,
    name: str,
    date: str,
    desc: str,
    amount: str,
    units: str,
    price: str,
    stamp: str = "0",
) -> dict[str, Any]:
    return {
        "email": "",
        "amc": "H",
        "amcName": "HDFC Mutual Fund",
        "folio": "17064219",
        "checkDigit": "63",
        "trxnDate": date,
        "postedDate": date,
        "scheme": scheme,
        "schemeName": name,
        "trxnDesc": desc,
        "trxnAmount": amount,
        "trxnUnits": units,
        "purchasePrice": price,
        "sttTax": "0",
        "tax": "0",
        "totalTax": "0",
        "trxnMode": "N",
        "stampDuty": stamp,
        "trxnCharge": "0",
        "trxnTypeFlag": "",
        "isin": isin,
    }


_EQ = ("02", "INF179K01608", "HDFC Flexi Cap Fund - Regular Plan - Growth")
_DT = ("54", "INF179K01442", "HDFC Low Duration Fund - Regular Plan - Growth")
_EL = ("21", "INF179K01BE2", "HDFC ELSS Tax Saver - Regular Plan - Growth")

# Deliberately varied: a non-financial row (must be dropped), a plain purchase,
# SIP instalments, a redemption, a switch pair, and a dividend reinvestment —
# so every branch of the classifier is exercised by just clicking through.
DETAILED_SAMPLE: dict[str, Any] = {
    "pan": "CFRPP0494R",
    "pekrn": "",
    "email": "",
    "fromDate": "01-Apr-1990",
    "toDate": "29-Aug-2024",
    "data": [
        {
            "dtTransaction": [
                _txn(
                    *_EQ, "24-SEP-2020", "Address Updated from KRA Data", "0", "0", "0"
                ),
                _txn(*_EQ, "10-OCT-2020", "Purchase", "20000", "22.951", "871.42", "1"),
                _txn(
                    *_EQ,
                    "05-NOV-2021",
                    "Systematic Investment Purchase",
                    "9000",
                    "10.331",
                    "871.28",
                    "0.45",
                ),
                _txn(
                    *_EQ,
                    "05-MAY-2022",
                    "Systematic Investment Purchase",
                    "9000",
                    "10.332",
                    "871.28",
                    "0.45",
                ),
                _txn(
                    *_DT, "04-FEB-2021", "Purchase", "60000", "1339.240", "44.80", "3"
                ),
                _txn(*_DT, "18-JUN-2023", "Redemption", "5000", "-100.000", "50.00"),
                _txn(
                    *_DT,
                    "20-JUN-2023",
                    "Switch Out - to HDFC ELSS Tax Saver",
                    "10000",
                    "-200.000",
                    "50.00",
                ),
                _txn(
                    *_EL,
                    "20-JUN-2023",
                    "Switch In - from HDFC Low Duration",
                    "10000",
                    "90.909",
                    "110.00",
                ),
                _txn(
                    *_EL, "15-MAR-2024", "IDCW Reinvestment", "450", "3.750", "120.00"
                ),
            ],
            "dtSummary": [
                {
                    "email": "",
                    "amc": "H",
                    "amcName": "HDFC Mutual Fund",
                    "folio": "17064219",
                    "scheme": "02",
                    "schemeName": _EQ[2],
                    "kycStatus": "1",
                    "brokerCode": "ARN-48944",
                    "brokerName": "A Distributor",
                    "rtaCode": "CAMS",
                    "decimalUnits": "3",
                    "decimalAmount": "2",
                    "decimalNav": "3",
                    "lastTrxnDate": "05-MAY-2022",
                    "openingBal": "0.000",
                    "marketValue": "65420.10",
                    "nav": "1500.000",
                    "closingBalance": "43.614",
                    "lastNavDate": "11-JUL-2024",
                    "isDemat": "N",
                    "assetType": "EQUITY",
                    "isin": _EQ[1],
                    "nomineeStatus": "N",
                    "taxStatus": "01",
                    "costValue": "38000",
                },
                {
                    "email": "",
                    "amc": "H",
                    "amcName": "HDFC Mutual Fund",
                    "folio": "17064219",
                    "scheme": "54",
                    "schemeName": _DT[2],
                    "kycStatus": "1",
                    "brokerCode": "ARN-48944",
                    "brokerName": "A Distributor",
                    "rtaCode": "CAMS",
                    "decimalUnits": "3",
                    "decimalAmount": "2",
                    "decimalNav": "4",
                    "lastTrxnDate": "20-JUN-2023",
                    "openingBal": "0.000",
                    "marketValue": "51937.00",
                    "nav": "50.0000",
                    "closingBalance": "1039.240",
                    "lastNavDate": "12-AUG-2024",
                    "isDemat": "N",
                    "assetType": "DEBT",
                    "isin": _DT[1],
                    "nomineeStatus": "N",
                    "taxStatus": "01",
                    "costValue": "45000",
                },
                {
                    "email": "",
                    "amc": "H",
                    "amcName": "HDFC Mutual Fund",
                    "folio": "17064219",
                    "scheme": "21",
                    "schemeName": _EL[2],
                    "kycStatus": "1",
                    "brokerCode": "ARN-48944",
                    "brokerName": "A Distributor",
                    "rtaCode": "CAMS",
                    "decimalUnits": "3",
                    "decimalAmount": "2",
                    "decimalNav": "4",
                    "lastTrxnDate": "15-MAR-2024",
                    "openingBal": "0.000",
                    "marketValue": "11362.31",
                    "nav": "120.0000",
                    "closingBalance": "94.659",
                    "lastNavDate": "12-AUG-2024",
                    "isDemat": "N",
                    "assetType": "EQUITY",
                    "isin": _EL[1],
                    "nomineeStatus": "N",
                    "taxStatus": "01",
                    "costValue": "10450",
                },
            ],
        }
    ],
    "investorDetails": {
        "address": {
            "address1": "12 Some Street",
            "address2": "Near Somewhere",
            "address3": "",
            "city": "HOWRAH",
            "district": "",
            "state": "West Bengal",
            "pincode": "711204",
            "country": "India",
        },
        "mobile": "+917874806606",
        "investorFirstName": "Testing",
        "investorMiddleName": "",
        "investorLastName": "XYZ",
        "email": "investor@example.com",
    },
}


# --------------------------------------------------------------------------- QR codec


def qr_bytes(req_id: str, variant: str) -> bytes:
    """A valid PNG with our marker appended after IEND.

    Decoders stop at IEND, so the trailer is invisible to an image viewer while
    surviving a download and re-upload byte for byte — which is exactly what the
    real QR has to do.
    """
    marker = json.dumps({"reqId": req_id, "variant": variant}).encode()
    return _PNG + b"MFCMOCK" + zlib.compress(marker)


def variant_from_qr(qr_base64: str) -> Optional[str]:
    try:
        blob = base64.b64decode(mfc_crypto.normalize_b64(qr_base64), validate=False)
    except Exception:  # noqa: BLE001
        return None
    if b"MFCMOCK" not in blob:
        # Any other image: treat it as a detailed statement rather than an error,
        # so the flow can also be walked with a hand-made file.
        return "detailed" if len(blob) > 100 else None
    try:
        marker = json.loads(zlib.decompress(blob.split(b"MFCMOCK", 1)[1]))
        return str(marker.get("variant") or "detailed")
    except Exception:  # noqa: BLE001
        return "detailed"


def qr_data_url(req_id: str, variant: str) -> str:
    return (
        "data:image/png;base64," + base64.b64encode(qr_bytes(req_id, variant)).decode()
    )


# --------------------------------------------------------------------------- router

router = APIRouter(tags=["MF Central (mock)"], include_in_schema=False)

# reqId -> the request payload we were handed, so validateQRCode can echo the
# investor's own PAN back the way the real service does.
_requests_seen: dict[str, dict[str, Any]] = {}
_counter = {"n": 3_100_000}

# reqId -> the consent session's OTP state. This lives ENTIRELY inside the mock
# consent site, mirroring where it lives at MFC: their hosted page issues the
# OTP, checks it, and never exposes either operation on the client API we
# integrate against. Nothing in `app/domains/ingestion/services/` may read this
# — if it ever does, the flow has stopped being reproducible against the real
# service, which hands us a redirect URL and tells us nothing more.
_otp_sessions: dict[str, dict[str, Any]] = {}

OTP_MAX_ATTEMPTS = 3


# The mock's consent OTP. A fixed, memorable code beats a derived one for the
# thing this is actually for — walking the flow repeatedly — so 123456 is the
# default. MFC's real UAT rule is still one env var away, for rehearsing against
# their sandbox: MFC_MOCK_OTP=uat.
DEFAULT_MOCK_OTP = "123456"


def otp_for_pan(pan: str) -> str:
    """MFC's UAT OTP rule: ``00`` + the last four digits of the PAN (guide p.26).

    Used only when ``MFC_MOCK_OTP=uat``. Kept because it is what the real UAT
    environment issues, and losing it would mean rediscovering it from the PDF.
    """
    digits = "".join(ch for ch in pan if ch.isdigit())[-4:]
    return f"00{digits}" if len(digits) == 4 else "000000"


def mock_otp_code(pan: str = "") -> str:
    """The code this mock will accept.

    ``MFC_MOCK_OTP`` overrides: any 4-8 digit string, or the literal ``uat`` to
    follow MFC's PAN-derived UAT rule.
    """
    import os

    raw = (os.getenv("MFC_MOCK_OTP") or "").strip()
    if raw.lower() == "uat":
        return otp_for_pan(pan)
    if raw.isdigit() and 4 <= len(raw) <= 8:
        return raw
    return DEFAULT_MOCK_OTP


def _issue_otp(req_id: str, pan: str = "") -> str:
    """Mint (or re-mint, on resend) the consent OTP for one request.

    Derived, never random: a reload of the consent page must not invalidate the
    code already on screen, and a resend has to produce something the investor
    can still use.
    """
    known = _otp_sessions.get(req_id, {}).get("pan") or ""
    resolved = pan or str(_requests_seen.get(req_id, {}).get("pan") or "") or known
    code = mock_otp_code(resolved)
    # The PAN is kept on the session so a RESEND re-derives the same code. It
    # reaches us on newCasRequest and again in the redirect payload, but a
    # resend carries only the reqId.
    _otp_sessions[req_id] = {
        "code": code,
        "pan": resolved,
        "attempts": 0,
        "verified": False,
    }
    return code


def _unwrap(body: dict[str, Any]) -> dict[str, Any]:
    """Verify our signature, then decrypt — MFC's order, so a bad signature is
    caught before the payload is trusted."""
    _, public_key = keys()
    encrypted = str(body.get("request") or "")
    signature = str(body.get("signature") or "")
    if not mfc_crypto.verify_signature(signature, encrypted, public_key_pem=public_key):
        raise HTTPException(
            status_code=422, detail={"code": "error", "message": "Invalid signature"}
        )
    try:
        return mfc_crypto.decrypt_api_payload(
            encrypted, shared_key=MOCK_ENCRYPTION_KEY, iv=MOCK_IV
        )
    except mfc_crypto.MfcCryptoError as exc:
        raise HTTPException(
            status_code=422, detail={"code": "error", "message": str(exc)}
        ) from exc


def _wrap(payload: Any) -> JSONResponse:
    private_key, _ = keys()
    encrypted = mfc_crypto.encrypt_api_payload(
        payload, shared_key=MOCK_ENCRYPTION_KEY, iv=MOCK_IV
    )
    signature = mfc_crypto.sign_detached_jws(
        encrypted, private_key_pem=private_key, kid="mock-key-1"
    )
    return JSONResponse({"signature": signature, "response": encrypted})


def _check_auth(authorization: Optional[str], client_id: Optional[str]) -> None:
    if not (authorization or "").startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    if client_id != MOCK_CLIENT_ID:
        raise HTTPException(status_code=401, detail="Unknown ClientId")


@router.post("/oauth/token")
async def token(request: Request):
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Basic "):
        raise HTTPException(status_code=401, detail="Basic auth required")
    try:
        decoded = base64.b64decode(auth[6:]).decode()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=401, detail="Malformed Basic auth") from exc
    if decoded != f"{MOCK_CLIENT_ID}:{MOCK_CLIENT_SECRET}":
        raise HTTPException(status_code=401, detail="Bad client credentials")
    form = await request.form()
    if (
        form.get("username") != MOCK_USERNAME
        or form.get("password") != MOCK_PASSWORD
        or form.get("grant_type") != "password"
    ):
        raise HTTPException(status_code=401, detail="Bad user credentials")
    return {
        "access_token": "mock-access-token",
        "token_type": "bearer",
        "refresh_token": "mock-refresh-token",
        "expires_in": 2591999,
        "scope": "read write trust",
    }


@router.post("/api/client/V1/newCasRequest")
async def new_cas_request(
    request: Request,
    authorization: Optional[str] = Header(default=None),
    clientid: Optional[str] = Header(default=None, alias="ClientId"),
):
    _check_auth(authorization, clientid)
    payload = _unwrap(await request.json())

    pan = str(payload.get("pan") or "").strip()
    if len(pan) != 10:
        raise HTTPException(
            status_code=400, detail={"code": "error", "message": "PAN is not valid"}
        )
    if bool(payload.get("mobile")) == bool(payload.get("email")):
        # MFC's documented rejection, reproduced because our client is supposed
        # to make it unreachable.
        raise HTTPException(
            status_code=400,
            detail={"code": "error", "message": "Pass exactly one of mobile or email"},
        )

    _counter["n"] += 1
    req_id = str(_counter["n"])
    _requests_seen[req_id] = payload
    # The real service sends this by SMS or email the moment the request is
    # registered — before the investor ever reaches the consent page.
    _issue_otp(req_id, pan)
    return _wrap(
        {
            "reqId": int(req_id),
            "otpRef": f"mock-otp-{req_id}",
            "userSubjectReference": "",
            "clientRefNo": payload.get("clientRefNo"),
        }
    )


@router.post("/api/client/V1/validateQRCode")
async def validate_qr(
    request: Request,
    authorization: Optional[str] = Header(default=None),
    clientid: Optional[str] = Header(default=None, alias="ClientId"),
):
    _check_auth(authorization, clientid)
    payload = _unwrap(await request.json())

    req_id = str(payload.get("reqId") or "")
    variant = variant_from_qr(str(payload.get("qrCode") or ""))
    if variant is None:
        raise HTTPException(
            status_code=400,
            detail={"code": "error", "message": "QR code could not be read"},
        )

    original = _requests_seen.get(req_id, {})
    body = dict(SUMMARY_SAMPLE if variant == "summary" else DETAILED_SAMPLE)
    body["reqId"] = req_id
    body["pan"] = original.get("pan") or body["pan"]
    if variant != "summary":
        body["fromDate"] = original.get("fromDate") or body["fromDate"]
        body["toDate"] = original.get("toDate") or body["toDate"]
    return _wrap(body)


@router.get("/api/auth/start", response_class=HTMLResponse)
async def auth_start(data: str = ""):
    """Stand-in for MFC's consent site.

    Decrypting the payload here is the point: it is the only local proof that our
    URL cipher (raw 32-byte key, random IV, hex:hex) is right, and that is a
    different cipher setup from the API envelope.
    """
    try:
        decoded = mfc_crypto.decrypt_redirect_payload(data, url_key=MOCK_URL_KEY)
    except Exception as exc:  # noqa: BLE001
        return HTMLResponse(
            f"<h2>Could not decrypt the redirect payload</h2><pre>{exc}</pre>",
            status_code=400,
        )

    req_id = str(decoded.get("reqId") or "")
    session = _otp_sessions.get(req_id)
    if session is None:
        # The page can be reloaded long after newCasRequest, or opened against a
        # reqId this process never issued (a --reload wiped the dict). Minting on
        # demand keeps the flow walkable instead of dead-ending on a blank OTP.
        _issue_otp(req_id, str(decoded.get("pan") or ""))
        session = _otp_sessions[req_id]

    return HTMLResponse(
        _render(
            _CONSENT_PAGE,
            REQ_ID=req_id,
            PAYLOAD=json.dumps(decoded, indent=2),
            DETAILED_QR=qr_data_url(req_id, "detailed"),
            SUMMARY_QR=qr_data_url(req_id, "summary"),
            REDIRECT_URL=str(decoded.get("redirectUrl") or ""),
            OTP_DEST=_mask_destination(decoded),
            OTP_HINT=str(session["code"]),
            # When Prozpr's own screen already cleared the OTP, this page must
            # not ask a second time — the real site would not, and a duplicate
            # gate is the difference between rehearsing the flow and inventing
            # a new one.
            START_PANE="pane-variant" if session.get("verified") else "pane-otp",
        )
    )


def _mask_destination(decoded: dict[str, Any]) -> str:
    """How the real consent page names where it sent the OTP."""
    mobile = str(decoded.get("mobile") or "").strip()
    if mobile:
        return f"xxxxxx{mobile[-4:]}" if len(mobile) >= 4 else mobile
    email = str(decoded.get("email") or "").strip()
    if email and "@" in email:
        name, _, domain = email.partition("@")
        return f"{name[:2]}xxx@{domain}"
    return "your registered contact"


# The consent site's OWN endpoints. Deliberately not under /api/client/V1/ —
# these are not part of the API surface we integrate against, and no Prozpr code
# may call them. They exist so the mock's OTP gate behaves like MFC's: a code
# that can be wrong, retried a bounded number of times, and resent.


@router.post("/api/auth/verify-otp")
async def verify_otp(request: Request):
    body = await request.json()
    req_id = str(body.get("reqId") or "")
    entered = re.sub(r"\D", "", str(body.get("otp") or ""))

    session = _otp_sessions.get(req_id)
    if session is None:
        return JSONResponse(
            {"status": "error", "message": "This request has expired. Start again."},
            status_code=410,
        )
    if session["verified"]:
        return {"status": "success", "remaining": 0}
    if session["attempts"] >= OTP_MAX_ATTEMPTS:
        return JSONResponse(
            {
                "status": "error",
                "message": "Too many incorrect attempts. Request a new code.",
                "remaining": 0,
            },
            status_code=429,
        )

    if entered != session["code"]:
        session["attempts"] += 1
        remaining = OTP_MAX_ATTEMPTS - session["attempts"]
        return JSONResponse(
            {
                "status": "error",
                "message": (
                    f"That code is not right. {remaining} attempt"
                    f"{'' if remaining == 1 else 's'} left."
                    if remaining
                    else "That code is not right. Request a new one."
                ),
                "remaining": remaining,
            },
            status_code=400,
        )

    session["verified"] = True
    session["attempts"] = 0
    return {"status": "success", "remaining": OTP_MAX_ATTEMPTS}


@router.post("/api/auth/resend-otp")
async def resend_otp(request: Request):
    body = await request.json()
    req_id = str(body.get("reqId") or "")
    code = _issue_otp(req_id)
    # Echoed only because there is no SMS here. The real site returns nothing
    # but an acknowledgement, which is why the frontend must never read it.
    return {"status": "success", "mockCode": code}


# MFC's own crypto helpers, so MFC_CRYPTO_MODE=remote can be exercised too.


@router.post("/api/test/encrypt", response_class=PlainTextResponse)
async def helper_encrypt(request: Request):
    raw = (await request.body()).decode()
    return mfc_crypto.encrypt_api_payload(
        json.loads(raw), shared_key=MOCK_ENCRYPTION_KEY, iv=MOCK_IV
    )


@router.post("/api/test/decrypt", response_class=PlainTextResponse)
async def helper_decrypt(request: Request):
    raw = (await request.body()).decode()
    return json.dumps(
        mfc_crypto.decrypt_api_payload(raw, shared_key=MOCK_ENCRYPTION_KEY, iv=MOCK_IV)
    )


@router.post("/api/test/generateSignature", response_class=PlainTextResponse)
async def helper_sign(request: Request):
    raw = (await request.body()).decode()
    private_key, _ = keys()
    return mfc_crypto.sign_detached_jws(raw, private_key_pem=private_key)


@router.get("/", response_class=HTMLResponse)
async def index():
    """A landing page, so hitting /mfc-mock explains what this is."""
    return HTMLResponse(
        _render(
            _INDEX_PAGE,
            API_BASE=Settings.get_mfc_api_base_url(),
            REDIRECT_BASE=Settings.get_mfc_redirect_base_url(),
            KEY_FILE=str(KEY_FILE),
        )
    )


# --------------------------------------------------------------------------- pages


def _render(template: str, **values: str) -> str:
    """``__NAME__`` substitution, because these pages carry CSS and JavaScript.

    ``str.format`` chokes on every ``{`` in a stylesheet or a script body, and
    doubling them all is a trap the next edit walks straight into. Values are
    HTML-escaped except the QR data URLs, which are base64 and cannot contain a
    metacharacter.
    """
    import html as _html

    out = template
    for name, value in values.items():
        safe = value if name.endswith("_QR") else _html.escape(value, quote=True)
        out = out.replace(f"__{name}__", safe)
    return out


_STYLE = """
  body { font: 14px/1.6 system-ui, -apple-system, Segoe UI, sans-serif; margin: 0;
         padding: 20px 16px; background: #f5f6f8; color: #16181d; }
  .card { max-width: 540px; margin: 0 auto; background: #fff; border-radius: 14px;
          padding: 20px; box-shadow: 0 1px 3px rgba(0,0,0,.09); }
  details { margin-top: 16px; }
  summary { font-size: 11px; color: #8b8f98; cursor: pointer; }
  h1 { font-size: 17px; margin: 0 0 4px; }
  p.sub { color: #6b7280; font-size: 12px; margin: 0 0 18px; }
  pre { background: #f2f3f5; border-radius: 8px; padding: 12px; font-size: 11px;
        overflow-x: auto; margin: 0 0 4px; }
  a.dl, button.dl { display: block; width: 100%; box-sizing: border-box;
        text-align: center; text-decoration: none; padding: 12px; border: 0;
        border-radius: 10px; font-weight: 600; margin-top: 10px; font-size: 13px;
        cursor: pointer; font-family: inherit; }
  .primary { background: #16181d; color: #fff; }
  .secondary { background: #eceef1; color: #16181d; }
  .note { font-size: 11px; color: #8b8f98; margin-top: 18px; line-height: 1.7; }
  code { background: #f2f3f5; padding: 1px 5px; border-radius: 4px; font-size: 11px; }
  ol { padding-left: 18px; font-size: 12px; color: #4b5057; }
"""

_CONSENT_PAGE = (
    """<!doctype html>
<html><head><meta charset="utf-8"><title>MF Central (mock) &mdash; consent</title>
<style>"""
    + _STYLE
    + """
  .pane { display: none; }
  .pane.on { display: block; }
  .otp { width: 100%; box-sizing: border-box; padding: 12px; font-size: 20px;
         letter-spacing: 8px; text-align: center; border: 1px solid #d6d9de;
         border-radius: 10px; font-family: ui-monospace, SFMono-Regular, monospace; }
  .otp:focus { outline: 0; border-color: #16181d; }
  .err { color: #b42318; font-size: 12px; margin: 8px 0 0; min-height: 16px; }
  .hint { background: #fff8e6; border: 1px solid #f2dfae; border-radius: 8px;
          padding: 10px 12px; font-size: 11px; color: #7a5c12; margin: 12px 0 0; }
  .link { background: none; border: 0; color: #4b5057; font-size: 11px;
          text-decoration: underline; cursor: pointer; padding: 8px 0 0;
          font-family: inherit; }
  .choice { display: block; border: 1px solid #d6d9de; border-radius: 10px;
            padding: 12px; margin-top: 10px; cursor: pointer; }
  .choice.sel { border-color: #16181d; background: #f7f8f9; }
  .choice b { font-size: 13px; }
  .choice span { display: block; font-size: 11px; color: #6b7280; margin-top: 2px; }
  .qrimg { display: block; margin: 14px auto 0; width: 132px; height: 132px;
           image-rendering: pixelated; border: 1px solid #e6e8eb;
           border-radius: 8px; background: #fff; }
</style></head>
<body><div class="card">
  <h1>MF Central &mdash; consent (mock)</h1>
  <p class="sub">Request __REQ_ID__</p>

  <!-- 1 ------------------------------------------------------------ OTP -->
  <div class="pane" id="pane-otp">
    <p style="font-size:13px;margin:0 0 14px">
      Enter the 6-digit code sent to <strong>__OTP_DEST__</strong> to authorise
      sharing your consolidated account statement.
    </p>
    <input class="otp" id="otp" inputmode="numeric" autocomplete="one-time-code"
           maxlength="6" placeholder="------" aria-label="One-time password">
    <p class="err" id="otp-err"></p>
    <button class="dl primary" id="verify" type="button">Verify</button>
    <button class="link" id="resend" type="button">Resend the code</button>
    <p class="hint">
      Mock only: no SMS is sent, so the code is
      <strong id="hint-code">__OTP_HINT__</strong>. Wrong codes are rejected and
      there are three attempts, because the real site behaves that way and the
      app has to survive it.
    </p>
  </div>

  <!-- 2 -------------------------------------------------------- variant -->
  <div class="pane" id="pane-variant">
    <p style="font-size:13px;margin:0 0 6px">Which statement do you want to share?</p>
    <label class="choice sel" id="c-detailed">
      <b>Detailed</b>
      <span>Holdings and full transaction history. Required to build returns.</span>
    </label>
    <label class="choice" id="c-summary">
      <b>Summary</b>
      <span>Valuations only, no transactions. Prozpr rejects this on purpose.</span>
    </label>
    <button class="dl primary" id="to-download" type="button">Continue</button>

    <details>
      <summary>Redirect payload, decrypted (proof our URL cipher is right)</summary>
      <pre style="margin-top:8px">__PAYLOAD__</pre>
    </details>
  </div>

  <!-- 3 ------------------------------------------------------- download -->
  <div class="pane" id="pane-download">
    <p style="font-size:13px;margin:0 0 2px">Your statement is ready.</p>
    <p class="sub">Download the QR code &mdash; it is how the statement is handed
    back, and it works once.</p>
    <img class="qrimg" id="qr-preview" alt="">
    <button class="dl primary" id="download" type="button">
      Download QR code
    </button>
    <button class="dl secondary" id="done" type="button">
      Done &mdash; return to Prozpr
    </button>
    <p class="note" id="dl-note">
      Handed straight back to Prozpr, and also saved as
      <code>cas-request-qr.png</code> so the download-and-find-it path stays
      walkable.
      <br><br>
      Returns to <code>__REDIRECT_URL__</code>
    </p>
  </div>
</div>
<script>
(function () {
  var REQ_ID = '__REQ_ID__';
  var QR = { detailed: '__DETAILED_QR__', summary: '__SUMMARY_QR__' };
  var variant = 'detailed';

  function show(id) {
    ['pane-otp', 'pane-variant', 'pane-download'].forEach(function (p) {
      document.getElementById(p).classList.toggle('on', p === id);
    });
  }
  show('__START_PANE__');
  function post(path, body) {
    return fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    }).then(function (r) {
      return r.json().then(function (j) { return [r.ok, j]; });
    });
  }

  var otp = document.getElementById('otp');
  var err = document.getElementById('otp-err');

  document.getElementById('verify').addEventListener('click', function () {
    err.textContent = '';
    post('verify-otp', { reqId: REQ_ID, otp: otp.value })
      .then(function (res) {
        if (res[0] && res[1].status === 'success') { show('pane-variant'); }
        else { err.textContent = res[1].message || 'Could not verify that code.'; }
      })
      .catch(function () { err.textContent = 'Network error. Try again.'; });
  });

  otp.addEventListener('keydown', function (e) {
    if (e.key === 'Enter') { document.getElementById('verify').click(); }
  });

  document.getElementById('resend').addEventListener('click', function () {
    err.textContent = '';
    post('resend-otp', { reqId: REQ_ID }).then(function (res) {
      document.getElementById('hint-code').textContent = res[1].mockCode || '';
      otp.value = '';
      otp.focus();
    });
  });

  ['detailed', 'summary'].forEach(function (v) {
    document.getElementById('c-' + v).addEventListener('click', function () {
      variant = v;
      document.getElementById('c-detailed').classList.toggle('sel', v === 'detailed');
      document.getElementById('c-summary').classList.toggle('sel', v === 'summary');
    });
  });

  document.getElementById('to-download').addEventListener('click', function () {
    document.getElementById('qr-preview').src = QR[variant];
    show('pane-download');
  });

  function dataUrlToBytes(url) {
    var b64 = url.slice(url.indexOf(',') + 1);
    var bin = atob(b64);
    var out = new Uint8Array(bin.length);
    for (var i = 0; i < bin.length; i++) { out[i] = bin.charCodeAt(i); }
    return out;
  }

  document.getElementById('download').addEventListener('click', function () {
    var url = QR[variant];
    var b64 = url.slice(url.indexOf(',') + 1);

    // 1. Hand the bytes straight to the host. This is MFC's OWN documented
    //    message for hosts that cannot take a file download (their
    //    platform-compatibility guide, "mfc-cas-download"); we post it to the
    //    opener/parent as well, which is the one extension the real service
    //    would need to make. A host that listens never touches the disk.
    var msg = { type: 'mfc-cas-download',
                data: { base64: b64, filename: 'cas-request-qr.png' } };
    try {
      if (window.opener) { window.opener.postMessage(msg, '*'); }
      else if (window.parent !== window) { window.parent.postMessage(msg, '*'); }
    } catch (e) { /* a host that isn't listening is not an error */ }

    // 2. Still save the file, because that is what a browser does today and
    //    the download-and-find-it path has to stay walkable. Blob, not data:.
    try {
      var blob = new Blob([dataUrlToBytes(url)], { type: 'image/png' });
      var href = URL.createObjectURL(blob);
      var a = document.createElement('a');
      a.href = href;
      a.download = 'cas-request-qr.png';
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      setTimeout(function () { URL.revokeObjectURL(href); }, 30000);
    } catch (e) {
      document.getElementById('dl-note').textContent =
        'Download failed in this browser: ' + e;
    }
  });

  // Mirrors what MFC's real app posts back, so the popup and iframe paths are
  // exercised here too, not only the standalone redirect.
  document.getElementById('done').addEventListener('click', function () {
    var msg = { type: 'mfc-cas-complete',
                data: { status: 'success', reqId: REQ_ID, type: variant } };
    if (window.opener) {
      window.opener.postMessage(msg, '*');
      window.close();
    } else if (window.parent !== window) {
      window.parent.postMessage(msg, '*');
    } else {
      window.location.href = '__REDIRECT_URL__?status=success&reqId=' + REQ_ID
        + '&type=' + variant;
    }
  });
})();
</script>
</body></html>
"""
)


_INDEX_PAGE = (
    """<!doctype html>
<html><head><meta charset="utf-8"><title>MF Central (mock)</title>
<style>"""
    + _STYLE
    + """</style></head>
<body><div class="card">
  <h1>MF Central — local mock</h1>
  <p class="sub">Active because no real MFC credentials are configured and this
  is not a production deployment.</p>

  <ol>
    <li>Open <code>/mfc-cas</code> in the app.</li>
    <li>Continue to MF Central — this mock opens in a pop-up.</li>
    <li>Download the <strong>Detailed</strong> QR.</li>
    <li>Upload it back in the app.</li>
  </ol>

  <p class="note">
    API base <code>__API_BASE__</code><br>
    Consent base <code>__REDIRECT_BASE__</code><br>
    RSA pair <code>__KEY_FILE__</code> (git-ignored, minted on first use)<br><br>
    This implements MF Central's documented protocol against local keys. It
    proves our wiring; it does not prove theirs.
  </p>
</div></body></html>
"""
)
