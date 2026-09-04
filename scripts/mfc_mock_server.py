"""DEV-ONLY — a stand-in for MF Central, so the integration can be run locally.

MFC issues UAT credentials by email after an LOI is signed, which means the
integration is otherwise untestable until commercial paperwork completes. This
server speaks their documented protocol exactly — same endpoints, same
encrypt/sign envelope, same response shapes — against keys you generate
yourself, so every part of our side can be exercised today: the token cache, the
envelope, the detached JWS, the adapter, the ingest, the whole frontend flow.

What it is NOT: a validator of MFC's real behaviour. It implements the guide and
their Postman capture. Where the live service differs (and it will, somewhere),
this will happily agree with us and the live one will not. It is a wiring test,
not a conformance test.

Run:

    python scripts/mfc_mock_server.py --generate-keys   # writes .env.mfc-mock
    python scripts/mfc_mock_server.py                   # serves on :9110

Then in .env:

    MFC_API_BASE_URL=http://127.0.0.1:9110
    MFC_REDIRECT_BASE_URL=http://127.0.0.1:9110
    MFC_REDIRECT_URL=http://localhost:8080/mfc-cas/callback
    ...plus the credentials printed by --generate-keys

The consent UI is mocked too: ``GET /api/auth/start`` renders a page that
decrypts the redirect payload (proving our URL cipher is right), lets you pick
Summary or Detailed, and hands you a downloadable QR PNG whose bytes encode the
chosen variant. ``validateQRCode`` reads that back, so the summary/detailed
branch is genuinely exercised end to end rather than hard-coded.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import zlib
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from fastapi import FastAPI, Header, HTTPException, Request  # noqa: E402
from fastapi.responses import HTMLResponse, JSONResponse  # noqa: E402

from app.domains.ingestion.services import mfc_crypto  # noqa: E402

# --------------------------------------------------------------------------- config

MOCK_CLIENT_ID = os.getenv("MFC_MOCK_CLIENT_ID", "prozpr-mock-client")
MOCK_CLIENT_SECRET = os.getenv("MFC_MOCK_CLIENT_SECRET", "prozpr-mock-secret")
MOCK_USERNAME = os.getenv("MFC_MOCK_USERNAME", "prozpr-mock-user")
MOCK_PASSWORD = os.getenv("MFC_MOCK_PASSWORD", "prozpr-mock-password")
MOCK_ENCRYPTION_KEY = os.getenv("MFC_MOCK_ENCRYPTION_KEY", "prozpr-mock-encryption-key")
MOCK_IV = os.getenv("MFC_MOCK_IV", "1234567890123456")
MOCK_URL_KEY = os.getenv("MFC_MOCK_URL_KEY", "odLZvcdVAhq+Re8UstN8u928xRQTE6ym")
KEY_FILE = _ROOT / ".mfc-mock-keys.json"

# The QR "image": a tiny valid PNG with the variant + reqId stuffed into a
# tEXt-like trailer. Real MFC encodes an opaque token; all that matters for
# wiring is that the bytes round-trip and are long enough to pass our guards.
_PNG_HEADER = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


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
                    "investorName": "John Doe",
                    "age": 39,
                    "mobile": "9550755111",
                    "email": "john@example.com",
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
                    "05-MAY-2022",
                    "Systematic Investment Purchase",
                    "18000",
                    "20.663",
                    "871.28",
                    "0.9",
                ),
                _txn(
                    *_DT, "04-FEB-2021", "Purchase", "60000", "1339.240", "44.80", "3"
                ),
                _txn(*_DT, "18-JUN-2023", "Redemption", "5000", "-100.000", "50.00"),
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
                    "lastTrxnDate": "18-JUN-2023",
                    "openingBal": "0.000",
                    "marketValue": "61924.00",
                    "nav": "50.0000",
                    "closingBalance": "1239.240",
                    "lastNavDate": "12-AUG-2024",
                    "isDemat": "N",
                    "assetType": "DEBT",
                    "isin": _DT[1],
                    "nomineeStatus": "N",
                    "taxStatus": "01",
                    "costValue": "55000",
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
        "investorFirstName": "JQLVAAIJNJSI",
        "investorMiddleName": "",
        "investorLastName": "",
        "email": "investor@example.com",
    },
}


# --------------------------------------------------------------------------- keys


def _load_keys() -> tuple[str, str]:
    if not KEY_FILE.exists():
        raise SystemExit(
            f"No mock keys at {KEY_FILE}. Run: python scripts/mfc_mock_server.py --generate-keys"
        )
    data = json.loads(KEY_FILE.read_text(encoding="utf-8"))
    return data["private_key"], data["public_key"]


def generate_keys() -> None:
    """Mint an RSA pair and print the .env block that pairs with this server.

    Both halves of the pair go to both sides: the mock verifies OUR signature
    with the public key and signs its responses with the private one, so a
    single pair stands in for MFC's two-key exchange.
    """
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

    escaped_private = private_pem.replace("\n", "\\n")
    escaped_public = public_pem.replace("\n", "\\n")
    block = f"""# --- MF Central: local mock (scripts/mfc_mock_server.py) ---
MFC_API_BASE_URL=http://127.0.0.1:9110
MFC_REDIRECT_BASE_URL=http://127.0.0.1:9110
MFC_ORIGIN=http://127.0.0.1:9110
MFC_REDIRECT_URL=http://localhost:8080/mfc-cas/callback
MFC_CLIENT_ID={MOCK_CLIENT_ID}
MFC_CLIENT_SECRET={MOCK_CLIENT_SECRET}
MFC_USERNAME={MOCK_USERNAME}
MFC_PASSWORD={MOCK_PASSWORD}
MFC_ENCRYPTION_KEY={MOCK_ENCRYPTION_KEY}
MFC_IV={MOCK_IV}
MFC_URL_ENCRYPTION_KEY={MOCK_URL_KEY}
MFC_PRIVATE_KEY={escaped_private}
MFC_PUBLIC_KEY={escaped_public}
MFC_VERIFY_RESPONSE_SIGNATURE=true
"""
    out = _ROOT / ".env.mfc-mock"
    out.write_text(block, encoding="utf-8")
    print(f"Keys written to {KEY_FILE}")
    print(f"Env block written to {out} — paste it into .env, then start the server.")


# --------------------------------------------------------------------------- app


def build_app() -> FastAPI:
    # NB: FastAPI resolves these annotations by NAME against module globals
    # (`from __future__ import annotations` turns them into strings), so the
    # imports have to be module-level. Importing them inside this function made
    # every `request: Request` parameter look like a query parameter — the whole
    # server answered 422.
    private_key, public_key = _load_keys()
    app = FastAPI(title="MF Central (mock)", docs_url="/docs")

    # reqId -> the request payload we were given, so validateQRCode can echo the
    # investor's PAN back the way the real service does.
    requests_seen: dict[str, dict[str, Any]] = {}
    counter = {"n": 3_100_000}

    def _unwrap(body: dict[str, Any]) -> dict[str, Any]:
        """Verify our signature, then decrypt — MFC's order, so a bad signature
        is caught before the payload is trusted."""
        encrypted = str(body.get("request") or "")
        signature = str(body.get("signature") or "")
        if not mfc_crypto.verify_signature(
            signature, encrypted, public_key_pem=public_key
        ):
            raise HTTPException(
                status_code=422,
                detail={"code": "error", "message": "Invalid signature"},
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
        encrypted = mfc_crypto.encrypt_api_payload(
            payload, shared_key=MOCK_ENCRYPTION_KEY, iv=MOCK_IV
        )
        signature = mfc_crypto.sign_detached_jws(
            encrypted, private_key_pem=private_key, kid="mock-key-1"
        )
        return JSONResponse({"signature": signature, "response": encrypted})

    def _check_auth(authorization: str | None, client_id: str | None) -> None:
        if not (authorization or "").startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Missing bearer token")
        if client_id != MOCK_CLIENT_ID:
            raise HTTPException(status_code=401, detail="Unknown ClientId")

    @app.post("/oauth/token")
    async def token(request: Request):
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Basic "):
            raise HTTPException(status_code=401, detail="Basic auth required")
        decoded = base64.b64decode(auth[6:]).decode()
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

    @app.post("/api/client/V1/newCasRequest")
    async def new_cas_request(
        request: Request,
        authorization: str | None = Header(default=None),
        clientid: str | None = Header(default=None, alias="ClientId"),
    ):
        _check_auth(authorization, clientid)
        payload = _unwrap(await request.json())

        pan = str(payload.get("pan") or "").strip()
        if len(pan) != 10:
            raise HTTPException(
                status_code=400,
                detail={"code": "error", "message": "PAN is not valid"},
            )
        if bool(payload.get("mobile")) == bool(payload.get("email")):
            # MFC's documented rejection, reproduced because our client is meant
            # to prevent it ever being sent.
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "error",
                    "message": "Pass exactly one of mobile or email",
                },
            )

        counter["n"] += 1
        req_id = str(counter["n"])
        requests_seen[req_id] = payload
        return _wrap(
            {
                "reqId": int(req_id),
                "otpRef": f"mock-otp-{req_id}",
                "userSubjectReference": "",
                "clientRefNo": payload.get("clientRefNo"),
            }
        )

    @app.post("/api/client/V1/validateQRCode")
    async def validate_qr(
        request: Request,
        authorization: str | None = Header(default=None),
        clientid: str | None = Header(default=None, alias="ClientId"),
    ):
        _check_auth(authorization, clientid)
        payload = _unwrap(await request.json())

        req_id = str(payload.get("reqId") or "")
        qr = str(payload.get("qrCode") or "")
        variant = _variant_from_qr(qr)
        if variant is None:
            raise HTTPException(
                status_code=400,
                detail={"code": "error", "message": "QR code could not be read"},
            )

        original = requests_seen.get(req_id, {})
        if variant == "summary":
            body = dict(SUMMARY_SAMPLE)
            body["reqId"] = req_id
            body["pan"] = original.get("pan") or body["pan"]
        else:
            body = dict(DETAILED_SAMPLE)
            body["reqId"] = req_id
            body["pan"] = original.get("pan") or body["pan"]
            body["fromDate"] = original.get("fromDate") or body["fromDate"]
            body["toDate"] = original.get("toDate") or body["toDate"]
        return _wrap(body)

    # ---------------------------------------------------------------- consent UI

    @app.get("/api/auth/start", response_class=HTMLResponse)
    async def auth_start(data: str = ""):
        """Stand-in for MFC's consent site.

        Decrypting the payload here is the point: it is the only way to prove
        our URL cipher (raw 32-byte key, random IV, hex:hex) is right, and that
        is a different cipher setup from the API envelope.
        """
        try:
            decoded = mfc_crypto.decrypt_redirect_payload(data, url_key=MOCK_URL_KEY)
        except Exception as exc:  # noqa: BLE001
            return HTMLResponse(
                f"<h2>Could not decrypt the redirect payload</h2><pre>{exc}</pre>",
                status_code=400,
            )

        req_id = str(decoded.get("reqId") or "")
        redirect_url = str(decoded.get("redirectUrl") or "")
        summary_qr = _qr_data_url(req_id, "summary")
        detailed_qr = _qr_data_url(req_id, "detailed")
        pretty = json.dumps(decoded, indent=2)
        return HTMLResponse(
            _CONSENT_PAGE.format(
                req_id=req_id,
                payload=pretty,
                detailed_qr=detailed_qr,
                summary_qr=summary_qr,
                redirect_url=redirect_url,
            )
        )

    # ---------------------------------------------------------------- helpers API

    @app.post("/api/test/encrypt")
    async def helper_encrypt(request: Request):
        raw = (await request.body()).decode()
        return HTMLResponse(
            mfc_crypto.encrypt_api_payload(
                json.loads(raw), shared_key=MOCK_ENCRYPTION_KEY, iv=MOCK_IV
            )
        )

    @app.post("/api/test/decrypt")
    async def helper_decrypt(request: Request):
        raw = (await request.body()).decode()
        return HTMLResponse(
            json.dumps(
                mfc_crypto.decrypt_api_payload(
                    raw, shared_key=MOCK_ENCRYPTION_KEY, iv=MOCK_IV
                )
            )
        )

    @app.post("/api/test/generateSignature")
    async def helper_sign(request: Request):
        raw = (await request.body()).decode()
        return HTMLResponse(
            mfc_crypto.sign_detached_jws(raw, private_key_pem=private_key)
        )

    return app


# --------------------------------------------------------------------------- QR codec


def _qr_bytes(req_id: str, variant: str) -> bytes:
    """A valid PNG with our marker appended after IEND.

    Decoders stop at IEND, so the trailer is invisible to an image viewer while
    still surviving a download and re-upload byte for byte — which is exactly
    what the real QR has to do.
    """
    marker = json.dumps({"reqId": req_id, "variant": variant}).encode()
    return _PNG_HEADER + b"MFCMOCK" + zlib.compress(marker)


def _variant_from_qr(qr_base64: str) -> str | None:
    try:
        blob = base64.b64decode(mfc_crypto.normalize_b64(qr_base64), validate=False)
    except Exception:  # noqa: BLE001
        return None
    if b"MFCMOCK" not in blob:
        # Any other image: treat it as a detailed statement rather than an error,
        # so the flow can be exercised with a hand-made file too.
        return "detailed" if len(blob) > 100 else None
    try:
        marker = json.loads(zlib.decompress(blob.split(b"MFCMOCK", 1)[1]))
        return str(marker.get("variant") or "detailed")
    except Exception:  # noqa: BLE001
        return "detailed"


def _qr_data_url(req_id: str, variant: str) -> str:
    return (
        "data:image/png;base64," + base64.b64encode(_qr_bytes(req_id, variant)).decode()
    )


_CONSENT_PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>MF Central (mock) — consent</title>
<style>
  body {{ font: 14px/1.5 system-ui, sans-serif; margin: 0; padding: 24px;
         background: #f6f7f9; color: #111; }}
  .card {{ max-width: 520px; margin: 0 auto; background: #fff; border-radius: 14px;
           padding: 22px; box-shadow: 0 1px 3px rgba(0,0,0,.08); }}
  h1 {{ font-size: 17px; margin: 0 0 4px; }}
  p.sub {{ color: #666; font-size: 12px; margin: 0 0 18px; }}
  pre {{ background: #f2f3f5; border-radius: 8px; padding: 12px; font-size: 11px;
         overflow-x: auto; }}
  a.dl {{ display: block; text-align: center; text-decoration: none; padding: 12px;
          border-radius: 10px; font-weight: 600; margin-top: 10px; font-size: 13px; }}
  a.primary {{ background: #111; color: #fff; }}
  a.secondary {{ background: #eceef1; color: #111; }}
  .note {{ font-size: 11px; color: #888; margin-top: 16px; line-height: 1.6; }}
  code {{ background: #f2f3f5; padding: 1px 4px; border-radius: 4px; }}
</style></head>
<body><div class="card">
  <h1>MF Central — consent (mock)</h1>
  <p class="sub">Request {req_id}. No OTP here; the real site asks for one.</p>

  <p style="font-size:12px;color:#444;margin:0 0 6px">
    The redirect payload decrypted, which proves the URL cipher is correct:
  </p>
  <pre>{payload}</pre>

  <a class="dl primary" href="{detailed_qr}" download="mfc-cas-detailed.png">
    Download QR — Detailed statement
  </a>
  <a class="dl secondary" href="{summary_qr}" download="mfc-cas-summary.png">
    Download QR — Summary statement
  </a>

  <p class="note">
    Upload the downloaded PNG back in Prozpr. The Detailed one imports; the
    Summary one is rejected on purpose, so both branches can be seen.
    <br><br>
    Return URL: <code>{redirect_url}</code>
  </p>
</div></body></html>
"""


# --------------------------------------------------------------------------- main


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--generate-keys",
        action="store_true",
        help="Mint an RSA pair and write the matching .env block, then exit.",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9110)
    args = parser.parse_args()

    if args.generate_keys:
        generate_keys()
        return

    import uvicorn

    print(f"MF Central mock on http://{args.host}:{args.port} — docs at /docs")
    uvicorn.run(build_app(), host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
