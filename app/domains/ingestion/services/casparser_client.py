"""Async HTTP client for the CAS Parser API (https://casparser.in).

Two capabilities the CAMS import flow uses:

1. ``POST /v4/kfintech/generate`` — ask KFintech/CAMS to EMAIL the investor a
   password-protected detailed CAS PDF (async on their side; the PDF lands in
   the user's inbox within a few minutes).
2. ``POST /v4/smart/parse`` — upload that PDF (+ password) and get back a
   unified JSON with folios, schemes, valuations and the full transaction
   ledger. This replaces the old in-process ``casparser``/``pymupdf`` parsing.

Auth is a single server-side ``x-api-key`` header (``CASPARSER_API_KEY``); the
docs' ``sandbox-with-json-responses`` key returns canned responses without
consuming credits. The client is inert unless the key is set
(``Settings.casparser_enabled()``); build it via ``get_casparser_client()``.

NOTE: the API signals many domain failures inside a **200** body as
``{"status": "failed", "msg": "..."}`` (bad password, unsupported format).
The client surfaces those verbatim; interpreting them is the caller's job
(see ``cams_cas_ingest._parse_cas_via_api``).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Optional

import httpx

from app.core.config import Settings

logger = logging.getLogger(__name__)

# Parsing a multi-decade CAS server-side can take tens of seconds — allow a
# generous read window (the frontend upload timeout is 90 s).
CASPARSER_TIMEOUT = httpx.Timeout(connect=10.0, read=75.0, write=60.0, pool=10.0)
CASPARSER_MAX_RETRIES = 3


class CasParserApiError(Exception):
    """casparser.in unreachable, unauthenticated, or non-2xx response."""

    def __init__(
        self, message: str, status_code: int | None = None, body: Any = None
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body

    @property
    def short_reason(self) -> str:
        """The API's own ``msg`` when present, else the exception text."""
        if isinstance(self.body, dict):
            msg = self.body.get("msg") or self.body.get("message")
            if msg:
                return str(msg)
        return str(self)


class CasParserConfigError(CasParserApiError):
    """``CASPARSER_API_KEY`` is not configured."""


class CasParserClient:
    """Thin wrapper: one ``httpx.AsyncClient`` + generic ``request``. Close with
    ``aclose()`` / ``async with``."""

    def __init__(self, base_url: str, api_key: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._http = httpx.AsyncClient(timeout=CASPARSER_TIMEOUT)

    async def __aenter__(self) -> "CasParserClient":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    async def request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        data: dict[str, Any] | None = None,
        files: dict[str, Any] | None = None,
        what: str = "",
    ) -> Any:
        """Call a casparser.in endpoint. Retries network errors and 5xx/429.
        Raises ``CasParserApiError`` with the parsed body on 4xx/5xx."""
        url = self._base_url + path
        headers = {"x-api-key": self._api_key}
        last_err = ""
        for attempt in range(CASPARSER_MAX_RETRIES):
            t0 = time.monotonic()
            try:
                resp = await self._http.request(
                    method, url, headers=headers, json=json, data=data, files=files
                )
            except httpx.HTTPError as exc:
                last_err = str(exc)
                await asyncio.sleep(0.5 * (attempt + 1))
                continue

            ms = int((time.monotonic() - t0) * 1000)
            if resp.status_code in (429,) or resp.status_code >= 500:
                last_err = f"HTTP {resp.status_code}: {resp.text[:200]}"
                logger.warning(
                    "!!! CASPARSER-API !!! %s %s -> %d (%dms) :: retrying (%s)",
                    method,
                    path,
                    resp.status_code,
                    ms,
                    what or "call",
                )
                await asyncio.sleep(0.75 * (attempt + 1))
                continue

            body: Any
            try:
                body = resp.json()
            except ValueError:
                body = resp.text

            if resp.status_code >= 400:
                logger.warning(
                    "!!! CASPARSER-API !!! %s %s -> %d (%dms) :: %s",
                    method,
                    path,
                    resp.status_code,
                    ms,
                    what or "call failed",
                )
                raise CasParserApiError(
                    f"CAS Parser API {method} {path} -> {resp.status_code}",
                    status_code=resp.status_code,
                    body=body,
                )

            logger.info(
                ">>> CASPARSER-API >>> %s %s -> %d (%dms) :: %s",
                method,
                path,
                resp.status_code,
                ms,
                what or "ok",
            )
            return body

        raise CasParserApiError(f"CAS Parser API unreachable: {last_err}")

    # ── endpoints ────────────────────────────────────────────────────────────

    async def generate_kfintech_cas(
        self,
        *,
        email: str,
        password: str,
        from_date: str,
        to_date: str,
        pan_no: Optional[str] = None,
    ) -> dict[str, Any]:
        """Ask KFintech/CAMS to email the investor a detailed CAS PDF protected
        with *password*. Async on the registrar's side — the PDF reaches
        *email* within a few minutes. Dates are ``YYYY-MM-DD``."""
        payload: dict[str, Any] = {
            "email": email,
            "password": password,
            "from_date": from_date,
            "to_date": to_date,
        }
        if pan_no:
            payload["pan_no"] = pan_no
        body = await self.request(
            "POST",
            "/v4/kfintech/generate",
            json=payload,
            what=f"Request CAS statement mailed to {email}",
        )
        return body if isinstance(body, dict) else {"status": "success"}

    async def smart_parse(
        self, file_bytes: bytes, filename: str, password: str
    ) -> dict[str, Any]:
        """Parse a CAS PDF (auto-detects CAMS/KFintech vs CDSL vs NSDL) into the
        unified JSON (meta / investor / summary / mutual_funds / ...)."""
        body = await self.request(
            "POST",
            "/v4/smart/parse",
            files={"file": (filename, file_bytes, "application/pdf")},
            data={"password": password},
            what=f"Parse CAS PDF ({len(file_bytes) // 1024} KB)",
        )
        if not isinstance(body, dict):
            raise CasParserApiError(
                "CAS Parser API returned a non-JSON parse response", body=body
            )
        return body

    async def credits(self) -> dict[str, Any]:
        """Remaining API credits — ops/debug visibility."""
        body = await self.request("POST", "/v1/credits", json={}, what="Check credits")
        return body if isinstance(body, dict) else {}


_client: Optional[CasParserClient] = None


def get_casparser_client() -> CasParserClient:
    """Process-wide singleton (one connection pool). Raises
    ``CasParserConfigError`` when ``CASPARSER_API_KEY`` is unset."""
    global _client
    api_key = Settings.get_casparser_api_key()
    if not api_key:
        raise CasParserConfigError("CASPARSER_API_KEY is not configured")
    if _client is None:
        _client = CasParserClient(Settings.get_casparser_base_url(), api_key)
    return _client
