"""Async HTTP client for the MF Central (mfcentral.com) client APIs.

Three calls carry the whole CAS flow:

1. ``POST /oauth/token`` — password-grant OAuth with the client id/secret as
   HTTP Basic. The token lives 30 days; it is cached in-process and refreshed
   a day early rather than on the first 401, because a 401 mid-flow costs the
   investor their consent session.
2. ``POST /api/client/V1/newCasRequest`` — registers the request and returns
   ``reqId`` + ``otpRef``, which are what the investor's consent session is
   keyed on.
3. ``POST /api/client/V1/validateQRCode`` — exchanges the QR the investor
   downloaded for the actual CAS payload.

Every one of 2 and 3 is wrapped in the encrypt/sign envelope described in
:mod:`mfc_crypto`; :meth:`MfcClient.call_signed` is the only place that knows
the wire shape, so a change to the envelope touches one function.

``MFC_CRYPTO_MODE=remote`` swaps our local crypto for MFC's own
``/api/test/{encrypt,generateSignature,decrypt}`` helper endpoints — see the
setting's docstring for when that is worth three extra round-trips.

The client is inert unless the full credential set is configured
(``Settings.mfc_enabled()``); build it with :func:`get_mfc_client`.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import time
from typing import Any, Optional

import httpx

from app.core.config import Settings
from app.domains.ingestion.services import mfc_crypto
from app.domains.ingestion.services.mfc_crypto import MfcCryptoError

logger = logging.getLogger(__name__)

# MFC assembles a multi-decade statement synchronously behind validateQRCode;
# their own guide warns it is the slow call. The frontend waits 120 s.
# read=150: MFC's UAT has been seen to sit on newCasRequest for 91s before
# answering (2026-09-29, every request, their side). A 90s read ceiling turned
# that into 'could not reach MF Central' and hid the actual response.
MFC_TIMEOUT = httpx.Timeout(connect=10.0, read=150.0, write=30.0, pool=10.0)
MFC_MAX_RETRIES = 3

# Refresh a day before the nominal 30-day expiry. A token that dies between the
# newCasRequest and the validateQRCode strands an investor who has already
# consented, and re-consenting is the one step we cannot do on their behalf.
_TOKEN_REFRESH_SKEW_SECONDS = 24 * 3600


class MfcApiError(Exception):
    """MFC unreachable, unauthenticated, or a non-2xx / business-error response."""

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        body: Any = None,
        *,
        stage: str | None = None,
        timed_out: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body
        # MFC took longer than our read ceiling. Distinct from "unreachable":
        # the request may have been processed, so the caller must not repeat it.
        self.timed_out = timed_out
        # Which leg failed (token / newCasRequest / validateQRCode / decrypt) —
        # the flow surfaces different user-facing copy per leg.
        self.stage = stage

    @property
    def short_reason(self) -> str:
        """MFC's own message when present, else the exception text.

        MFC's business errors arrive as ``{"errors": [{"code", "message"}]}``
        (often inside the encrypted response body, which ``_unwrap`` decrypts and
        stashes here), so that shape is checked first — it carries the specific,
        actionable text like "Invalid PAN/PEKRN, Mobile/Email combination" that
        the flat keys miss.
        """
        if isinstance(self.body, dict):
            errors = self.body.get("errors")
            if isinstance(errors, list) and errors:
                first = errors[0]
                if isinstance(first, dict) and first.get("message"):
                    return str(first["message"])
            for key in ("message", "msg", "error_description", "errorMessage", "error"):
                value = self.body.get(key)
                if value:
                    return str(value)
        return str(self)


class MfcConfigError(MfcApiError):
    """The MFC credential set is incomplete."""


class MfcClient:
    """One ``httpx.AsyncClient`` plus the envelope. Close with ``aclose()``."""

    def __init__(
        self,
        *,
        base_url: str,
        client_id: str,
        client_secret: str,
        username: str,
        password: str,
        encryption_key: str,
        iv: str,
        private_key: str,
        public_key: Optional[str] = None,
        origin: Optional[str] = None,
        signature_mode: str = "detached",
        signature_kid: Optional[str] = None,
        crypto_mode: str = "local",
        signature_source: Optional[str] = None,
        verify_response_signature: bool = False,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._client_id = client_id
        self._client_secret = client_secret
        self._username = username
        self._password = password
        self._encryption_key = encryption_key
        self._iv = iv
        self._private_key = private_key
        self._public_key = public_key
        self._origin = origin
        self._signature_mode = signature_mode
        self._signature_kid = signature_kid
        self._crypto_mode = crypto_mode
        # Where the signature is produced, independent of the encrypt/decrypt
        # mode. Defaults to following crypto_mode; set to "local" to keep
        # signing in-process while encryption/decryption stay remote — the
        # working UAT arrangement, since MFC's sign helper 500s and MFC verifies
        # against the public key WE registered (their responses carry our kid).
        self._signature_source = (signature_source or crypto_mode).strip().lower()
        self._verify_response_signature = verify_response_signature

        self._http = httpx.AsyncClient(timeout=MFC_TIMEOUT)
        self._token: Optional[str] = None
        self._token_expires_at: float = 0.0
        self._token_lock = asyncio.Lock()

    async def __aenter__(self) -> "MfcClient":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    # ----------------------------------------------------------------- token

    async def _access_token(self, *, force: bool = False) -> str:
        """Cached OAuth token, refreshed a day before expiry.

        The lock keeps a burst of concurrent requests from each minting their
        own token — MFC rate-limits ``/oauth/token`` more tightly than the
        client APIs, and every extra token invalidates nothing but wastes the
        window.
        """
        async with self._token_lock:
            now = time.monotonic()
            if not force and self._token and now < self._token_expires_at:
                return self._token

            basic = base64.b64encode(
                f"{self._client_id}:{self._client_secret}".encode("utf-8")
            ).decode("ascii")
            try:
                response = await self._http.post(
                    f"{self._base_url}/oauth/token",
                    headers={
                        "Authorization": f"Basic {basic}",
                        "Content-Type": "application/x-www-form-urlencoded",
                    },
                    data={
                        "username": self._username,
                        "password": self._password,
                        "grant_type": "password",
                    },
                )
            except httpx.HTTPError as exc:
                raise MfcApiError(
                    f"Could not reach MF Central to authenticate: {exc}",
                    stage="token",
                ) from exc

            body = _safe_json(response)
            if response.status_code >= 400:
                raise MfcApiError(
                    f"MF Central rejected our credentials (HTTP {response.status_code}).",
                    status_code=response.status_code,
                    body=body,
                    stage="token",
                )
            token = str((body or {}).get("access_token") or "").strip()
            if not token:
                raise MfcApiError(
                    "MF Central returned no access_token.",
                    status_code=response.status_code,
                    body=body,
                    stage="token",
                )

            expires_in = _int_or((body or {}).get("expires_in"), default=30 * 24 * 3600)
            self._token = token
            self._token_expires_at = now + max(
                60.0, float(expires_in) - _TOKEN_REFRESH_SKEW_SECONDS
            )
            return token

    # ----------------------------------------------------------------- envelope

    async def _encrypt(self, payload: Any) -> str:
        if self._crypto_mode == "remote":
            return await self._helper_call("encrypt", payload, key=self._encryption_key)
        return mfc_crypto.encrypt_api_payload(
            payload, shared_key=self._encryption_key, iv=self._iv
        )

    async def _decrypt(self, encrypted: str) -> Any:
        if self._crypto_mode == "remote":
            import json

            raw = await self._helper_call(
                "decrypt", encrypted, key=self._encryption_key, raw_text=True
            )
            try:
                return json.loads(raw)
            except Exception as exc:  # noqa: BLE001
                raise MfcApiError(
                    "MF Central's decrypt helper did not return JSON.",
                    body=raw,
                    stage="decrypt",
                ) from exc
        return mfc_crypto.decrypt_api_payload(
            encrypted, shared_key=self._encryption_key, iv=self._iv
        )

    async def _sign(self, encrypted_request: str) -> str:
        if self._signature_source == "remote":
            return await self._helper_call(
                "generateSignature", encrypted_request, key="PrivateKey", raw_text=True
            )
        if self._signature_mode == "jwt":
            return mfc_crypto.sign_compact_jwt(
                encrypted_request, private_key_pem=self._private_key
            )
        return mfc_crypto.sign_detached_jws(
            encrypted_request,
            private_key_pem=self._private_key,
            kid=self._signature_kid,
        )

    async def _helper_call(
        self, path: str, body: Any, *, key: str, raw_text: bool = False
    ) -> str:
        """One of MFC's ``/api/test/*`` crypto helpers.

        They take ``text/plain`` bodies and a ``key`` header carrying the actual
        secret to apply (the shared encryption key for encrypt/decrypt), and
        return the result as a bare string. Passing the *name* of the secret
        instead of its value encrypts under the wrong key and the client APIs
        then reject the payload with a 422 "Unable to decrypt" — verified
        against UAT, which is why the callers pass ``self._encryption_key``.
        """
        import json

        token = await self._access_token()
        payload = body if isinstance(body, str) else json.dumps(body)
        try:
            response = await self._http.post(
                f"{self._base_url}/api/test/{path}",
                headers={
                    "Content-Type": "text/plain",
                    "Authorization": f"Bearer {token}",
                    "ClientId": self._client_id,
                    "key": key,
                },
                content=payload.encode("utf-8"),
            )
        except httpx.HTTPError as exc:
            raise MfcApiError(
                f"Could not reach MF Central's {path} helper: {exc}", stage=path
            ) from exc
        if response.status_code >= 400:
            raise MfcApiError(
                f"MF Central's {path} helper failed (HTTP {response.status_code}).",
                status_code=response.status_code,
                body=_safe_json(response) or response.text,
                stage=path,
            )
        return response.text.strip()

    # ----------------------------------------------------------------- calls

    async def call_signed(
        self, path: str, payload: dict[str, Any], *, stage: str
    ) -> Any:
        """Encrypt + sign ``payload``, POST it, verify + decrypt the response.

        Retries only on transport errors and 5xx: a 4xx from MFC is a business
        rejection (bad PAN, expired reqId, already-consumed QR) that will say
        the same thing on the second attempt, and the QR ones are single-use.
        """
        encrypted = await self._encrypt(payload)
        signature = await self._sign(encrypted)
        token = await self._access_token()

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
            "ClientId": self._client_id,
        }
        if self._origin:
            headers["Origin"] = self._origin

        url = f"{self._base_url}{path}"
        body = {"signature": signature, "request": encrypted}

        last_exc: Exception | None = None
        for attempt in range(1, MFC_MAX_RETRIES + 1):
            try:
                response = await self._http.post(url, headers=headers, json=body)
            except httpx.TimeoutException as exc:
                # NEVER re-send a signed payload after a timeout. MFC may well
                # have processed the first attempt (2026-09-29: newCasRequest
                # took >150s to answer, the retry re-sent the same clientRefNo,
                # and MFC replied "Duplicate clientRefNo" — a double
                # registration reported as our mistake). validateQRCode is
                # worse: a retry would try to spend a QR the first attempt may
                # already have consumed. Surface the wait honestly instead.
                seconds = self._http.timeout.read or 0
                raise MfcApiError(
                    f"MF Central did not answer within {seconds:.0f}s.",
                    stage=stage,
                    timed_out=True,
                ) from exc
            except httpx.HTTPError as exc:
                last_exc = exc
                if attempt == MFC_MAX_RETRIES:
                    break
                await asyncio.sleep(0.5 * attempt)
                continue

            if response.status_code == 401 and attempt == 1:
                # The cached token died early — mint a new one and re-sign once.
                headers["Authorization"] = (
                    f"Bearer {await self._access_token(force=True)}"
                )
                continue

            if response.status_code >= 500 and attempt < MFC_MAX_RETRIES:
                await asyncio.sleep(0.5 * attempt)
                continue

            return await self._unwrap(response, stage=stage)

        raise MfcApiError(
            f"Could not reach MF Central: {last_exc}", stage=stage
        ) from last_exc

    async def _unwrap(self, response: httpx.Response, *, stage: str) -> Any:
        parsed = _safe_json(response)
        if response.status_code >= 400:
            # MFC's business errors (e.g. "Invalid PAN/PEKRN, Mobile/Email
            # combination") are ENCRYPTED in the `response` field of the error
            # body, exactly like a success payload. Decrypt it so `short_reason`
            # can surface the specific message instead of a generic HTTP reason —
            # otherwise every 4xx reads as "invalid input or format" and the
            # user has no idea which field to fix.
            body: Any = parsed if parsed is not None else response.text
            if isinstance(parsed, dict) and parsed.get("response"):
                inner = str(parsed["response"]).strip()
                # Seen live (2026-09-29): a 422 whose `response` is PLAINTEXT
                # JSON — `{"reqId":…,"errors":[{"message":"Duplicate clientRefNo"}]}`
                # — not ciphertext. Decrypting that raised, the fallback kept
                # the envelope, and the user read "invalid input or format".
                # So: try it as JSON first, decrypt only if it is not, and
                # parse a decrypted STRING too (the helper returns text).
                unwrapped: Any = _json_or_none(inner)
                if unwrapped is None:
                    try:
                        unwrapped = await self._decrypt(inner)
                    except Exception:  # noqa: BLE001 — fall back to the raw envelope
                        unwrapped = None
                if isinstance(unwrapped, str):
                    unwrapped = _json_or_none(unwrapped)
                # Seen live 2026-09-29: the DECRYPTED error is itself wrapped —
                # `{"response": "{\"reqId\":…,\"errors\":[…]}"}` — so the
                # errors list sits one envelope deeper than the success shape.
                unwrapped = _peel_response(unwrapped)
                if isinstance(unwrapped, dict):
                    body = unwrapped
            detail = ""
            if isinstance(body, dict):
                errs = body.get("errors")
                if isinstance(errs, list) and errs and isinstance(errs[0], dict):
                    detail = str(errs[0].get("message") or "")
            raise MfcApiError(
                detail or _http_reason(response.status_code),
                status_code=response.status_code,
                body=body,
                stage=stage,
            )
        if not isinstance(parsed, dict):
            raise MfcApiError(
                "MF Central returned a body we could not read.",
                status_code=response.status_code,
                body=response.text,
                stage=stage,
            )

        encrypted = str(parsed.get("response") or "").strip()
        if not encrypted:
            # 202 Accepted legitimately carries no body (the consent leg does
            # this); anything else with no `response` is an error envelope.
            if response.status_code == 202:
                return {}
            raise MfcApiError(
                "MF Central returned no response payload.",
                status_code=response.status_code,
                body=parsed,
                stage=stage,
            )

        signature = str(parsed.get("signature") or "")
        if self._public_key:
            verified = mfc_crypto.verify_signature(
                signature, encrypted, public_key_pem=self._public_key
            )
            if not verified:
                if self._verify_response_signature:
                    raise MfcApiError(
                        "MF Central's response signature did not verify.",
                        status_code=response.status_code,
                        stage=stage,
                    )
                logger.warning(
                    "MFC %s response signature did not verify (continuing: "
                    "MFC_VERIFY_RESPONSE_SIGNATURE is off)",
                    stage,
                )

        try:
            return await self._decrypt(encrypted)
        except MfcCryptoError as exc:
            raise MfcApiError(
                f"Could not decrypt MF Central's response: {exc}",
                status_code=response.status_code,
                stage="decrypt",
            ) from exc

    async def new_cas_request(
        self,
        *,
        client_ref_no: str,
        pan: str,
        mobile: Optional[str],
        email: Optional[str],
        from_date: str,
        to_date: str,
        redirect_url: str,
        pekrn: str = "",
    ) -> dict[str, Any]:
        """Register a CAS request. Returns ``{reqId, otpRef, clientRefNo, ...}``.

        MFC requires exactly one of email/mobile — passing both is a documented
        rejection, so the caller picks and this asserts it rather than silently
        dropping one.
        """
        if bool(mobile) == bool(email):
            raise MfcApiError(
                "MF Central needs exactly one of mobile or email, not both or neither.",
                stage="newCasRequest",
            )
        payload = {
            "clientRefNo": client_ref_no,
            "reqId": "",
            "pan": pan,
            "pekrn": pekrn,
            "email": email or "",
            "mobile": mobile or "",
            "fromDate": from_date,
            "toDate": to_date,
            "redirectUrl": redirect_url,
        }
        result = await self.call_signed(
            "/api/client/V1/newCasRequest", payload, stage="newCasRequest"
        )
        if not isinstance(result, dict) or not result.get("reqId"):
            raise MfcApiError(
                "MF Central did not return a request id.",
                body=result,
                stage="newCasRequest",
            )
        return result

    async def validate_qr_code(
        self, *, req_id: str, client_ref_no: str, qr_code_base64: str
    ) -> dict[str, Any]:
        """Exchange the investor's QR for the CAS payload (summary or detailed)."""
        payload = {
            "reqId": str(req_id),
            "clientRefNo": client_ref_no,
            "qrCode": qr_code_base64,
        }
        result = await self.call_signed(
            "/api/client/V1/validateQRCode", payload, stage="validateQRCode"
        )
        if not isinstance(result, dict):
            raise MfcApiError(
                "MF Central returned an unexpected CAS payload.",
                body=result,
                stage="validateQRCode",
            )
        return result

    def build_redirect_url(
        self,
        *,
        req_id: str,
        otp_ref: str,
        client_ref_no: str,
        pan: str,
        email: Optional[str],
        mobile: Optional[str],
        redirect_url: str,
        redirect_base_url: str,
        url_encryption_key: str,
    ) -> str:
        """The ``/api/auth/start?data=...`` URL the investor is sent to.

        Note the second, incompatible cipher — see :mod:`mfc_crypto`. ``token``
        is in the payload because MFC's schema requires the key to be present;
        their guide says to leave it blank.
        """
        data = mfc_crypto.encrypt_redirect_payload(
            {
                "reqId": str(req_id),
                "otpRef": otp_ref,
                "token": "",
                "pan": pan,
                "email": email or "",
                "mobile": mobile or "",
                "clientRefNo": client_ref_no,
                "redirectUrl": redirect_url,
            },
            url_key=url_encryption_key,
        )
        from urllib.parse import quote

        return f"{redirect_base_url.rstrip('/')}/api/auth/start?data={quote(data, safe='')}"


# --------------------------------------------------------------------------- helpers


def _json_or_none(text: str) -> Any:
    """Parse `text` as JSON, or None when it is not (ciphertext, HTML, blank)."""
    import json

    if not text or text[0] not in "{[":
        return None
    try:
        return json.loads(text)
    except ValueError:
        return None


def _peel_response(obj: Any, depth: int = 3) -> Any:
    """Unwrap `{"response": "<json text>"}` envelopes, nested up to `depth`.

    MFC wraps twice on the error path (the ciphertext decrypts to another
    `response` envelope whose value is JSON text). Stops at the first dict that
    is not exactly that shape, so a real payload is never disturbed.
    """
    for _ in range(depth):
        if not (isinstance(obj, dict) and set(obj) == {"response"}):
            return obj
        inner = obj["response"]
        parsed = _json_or_none(inner.strip()) if isinstance(inner, str) else inner
        if parsed is None:
            return obj
        obj = parsed
    return obj


def _safe_json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except Exception:  # noqa: BLE001 — MFC error pages are sometimes HTML
        return None


def _int_or(value: Any, *, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _http_reason(status_code: int) -> str:
    """MFC's documented status-code table, worded for a log line."""
    return {
        400: "MF Central rejected the request (business validation error).",
        401: "MF Central rejected our token or client id.",
        404: "MF Central endpoint not found — check the base URL.",
        422: "MF Central could not process the request (invalid input or format).",
    }.get(status_code, f"MF Central returned HTTP {status_code}.")


# --------------------------------------------------------------------------- factory

_client: Optional[MfcClient] = None


def get_mfc_client() -> MfcClient:
    """Process-wide client. Raises :class:`MfcConfigError` when unconfigured."""
    global _client
    if _client is not None:
        return _client
    if not Settings.mfc_enabled():
        raise MfcConfigError(
            "MF Central is not configured on this server "
            "(MFC_CLIENT_ID / SECRET / USERNAME / PASSWORD / ENCRYPTION_KEY / IV / "
            "URL_ENCRYPTION_KEY / PRIVATE_KEY).",
            stage="config",
        )
    _client = MfcClient(
        base_url=Settings.get_mfc_api_base_url(),
        client_id=Settings.get_mfc_client_id() or "",
        client_secret=Settings.get_mfc_client_secret() or "",
        username=Settings.get_mfc_username() or "",
        password=Settings.get_mfc_password() or "",
        encryption_key=Settings.get_mfc_encryption_key() or "",
        iv=Settings.get_mfc_iv() or "",
        private_key=Settings.get_mfc_private_key() or "",
        public_key=Settings.get_mfc_public_key(),
        origin=Settings.get_mfc_origin_header(),
        signature_mode=Settings.get_mfc_signature_mode(),
        signature_kid=Settings.get_mfc_signature_kid(),
        crypto_mode=Settings.get_mfc_crypto_mode(),
        signature_source=Settings.get_mfc_signature_source(),
        verify_response_signature=Settings.get_mfc_verify_response_signature(),
    )
    return _client


async def close_mfc_client() -> None:
    """Release the shared client (lifespan shutdown / tests)."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
