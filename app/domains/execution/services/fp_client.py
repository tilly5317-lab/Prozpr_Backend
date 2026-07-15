"""Async HTTP client for Fintech Primitives (live-verified against the sandbox).

Auth (confirmed): ``POST {base}/v2/auth/{tenant}/token`` with a FORM body
``grant_type=client_credentials&client_id=...&client_secret=...`` -> JWT
``access_token`` (+``expires_in``). Every API call carries
``Authorization: Bearer ...`` and ``x-tenant-id``. JSON to the token endpoint
is a 400.

The client is inert unless FP_TENANT/FP_API_KEY/FP_API_SECRET are set
(``Settings.fp_enabled()``); build it via ``get_fp_client()``.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Optional

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)

FP_TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=10.0)
FP_TOKEN_REFRESH_SKEW = 60.0  # re-mint this many seconds before expiry
FP_MAX_RETRIES = 3


class FpError(Exception):
    """FP unreachable, unauthenticated, or non-2xx response."""

    def __init__(self, message: str, status_code: int | None = None, body: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class FpConfigError(FpError):
    """FP credentials missing (FP_TENANT / FP_API_KEY / FP_API_SECRET)."""


# --- FP-API console log -----------------------------------------------------
# Every FP call funnels through FpClient.request(), so we log one distinctive,
# greppable line per call there: what FP endpoint fired + what it does + the
# result. Prefix ">>> FP-API >>>" (ok) / "!!! FP-API !!!" (failed) makes it
# stand out in a flood of other logs (grep "FP-API"). ASCII only — the Windows
# console is cp1252 and chokes on emoji / box-drawing characters.
#
# Value tuple is (what-a-write-does, what-a-read-does); method picks which.
_FP_CALL_DESC: dict[str, tuple[str, str]] = {
    "investor_profiles": ("Create FP investor (identity + FATCA)", "Read investor profile"),
    "email_addresses": ("Attach investor email", "Read investor email"),
    "phone_numbers": ("Attach investor phone", "Read investor phone"),
    "addresses": ("Attach investor address", "Read investor address"),
    "bank_accounts": ("Attach investor bank account", "Read investor bank account"),
    "mf_investment_accounts": (
        "Open MF investment account (enables ordering)",
        "Read MF investment account",
    ),
    "mf_purchases": ("Place one-time BUY (lumpsum) order", "Poll BUY order status"),
    "mf_purchase_plans": ("Start SIP (recurring buy plan)", "Poll SIP plan status"),
    "mf_redemptions": ("Place SELL (redemption) order", "Poll redemption status"),
    "mf_switches": ("Place SWITCH (scheme-to-scheme, same AMC)", "Poll switch status"),
    "mf_redemption_plans": ("Start SWP (systematic withdrawal)", "Poll SWP plan status"),
    "mf_switch_plans": ("Start STP (systematic transfer)", "Poll STP plan status"),
    "mf_folios": ("List investor folios (holdings)", "List investor folios (holdings)"),
    "mf_scheme_plans": ("Resolve scheme/plan by ISIN", "Resolve scheme/plan by ISIN"),
    "mf_settlement_details": ("Read order settlement details", "Read order settlement details"),
}


def _describe_fp_call(method: str, path: str) -> str:
    """One-line, human description of what an FP endpoint does (for the log)."""
    m = method.upper()
    p = path.split("?", 1)[0]
    if "/auth/" in p and p.endswith("/token"):
        return "Mint tenant access token (server-to-server auth)"
    if "/pre_verifications" in p:
        return (
            "KYC Pre-Verification: submit PAN/name/DOB for identity match"
            if m == "POST"
            else "KYC Pre-Verification: poll identity-match result"
        )
    for seg in (s for s in p.split("/") if s):
        desc = _FP_CALL_DESC.get(seg)
        if desc:
            return desc[1] if m == "GET" else desc[0]
    return m + " " + p


def _short_fp_error(body: Any) -> str:
    """Pull a short, single-line reason out of an FP error body for the log."""
    if isinstance(body, dict):
        err = body.get("error") or body
        if isinstance(err, dict):
            errs = err.get("errors")
            if isinstance(errs, list) and errs and isinstance(errs[0], dict):
                first = errs[0]
                return (
                    str(first.get("field", "")) + " " + str(first.get("message", ""))
                ).strip()
            if err.get("message"):
                return str(err["message"])
    return "see response"


class FpClient:
    """Thin wrapper: cached tenant token + generic ``request``. One instance owns
    one ``httpx.AsyncClient`` — close with ``aclose()`` / ``async with``."""

    def __init__(self, base_url: str, tenant: str, client_id: str, client_secret: str):
        self._base_url = base_url.rstrip("/")
        self._tenant = tenant
        self._client_id = client_id
        self._client_secret = client_secret
        self._http = httpx.AsyncClient(timeout=FP_TIMEOUT)
        self._token: Optional[str] = None
        self._token_expires_at: float = 0.0
        self._lock = asyncio.Lock()

    async def __aenter__(self) -> "FpClient":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _ensure_token(self, force: bool = False) -> str:
        async with self._lock:
            now = time.monotonic()
            if (
                not force
                and self._token
                and now < self._token_expires_at - FP_TOKEN_REFRESH_SKEW
            ):
                return self._token
            url = self._base_url + "/v2/auth/" + self._tenant + "/token"
            try:
                resp = await self._http.post(
                    url,
                    headers={"x-tenant-id": self._tenant},
                    data={
                        "grant_type": "client_credentials",
                        "client_id": self._client_id,
                        "client_secret": self._client_secret,
                    },
                )
            except httpx.HTTPError as exc:
                raise FpError("FP token request failed: " + str(exc)) from exc
            if resp.status_code >= 400:
                raise FpError(
                    "FP token mint -> " + str(resp.status_code) + ": " + resp.text,
                    status_code=resp.status_code,
                )
            data = resp.json()
            self._token = data["access_token"]
            ttl = float(data.get("expires_in") or 1800)
            self._token_expires_at = now + ttl
            logger.info(
                ">>> FP-API >>> POST /v2/auth/%s/token -> %d (%dms) :: "
                "Mint tenant access token (server auth, ttl~%.0fs)",
                self._tenant,
                resp.status_code,
                int((time.monotonic() - now) * 1000),
                ttl,
            )
            return self._token

    async def request(
        self,
        method: str,
        path: str,
        json: Any = None,
        params: dict | None = None,
    ) -> Any:
        """Call an FP resource (path like ``/v2/mf_purchases``). Retries network
        errors and 5xx/429; re-mints the token once on 401. Raises ``FpError``
        with the parsed body on 4xx/5xx."""
        url = self._base_url + path
        last_err: str = ""
        reminted = False
        for attempt in range(FP_MAX_RETRIES):
            token = await self._ensure_token()
            headers = {
                "Authorization": "Bearer " + token,
                "x-tenant-id": self._tenant,
                "Content-Type": "application/json",
            }
            t0 = time.monotonic()
            try:
                resp = await self._http.request(
                    method, url, headers=headers, json=json, params=params
                )
            except httpx.HTTPError as exc:
                last_err = str(exc)
                await asyncio.sleep(0.5 * (attempt + 1))
                continue
            dur_ms = int((time.monotonic() - t0) * 1000)
            if resp.status_code == 401 and not reminted:
                reminted = True
                await self._ensure_token(force=True)
                continue
            if resp.status_code in (429,) or resp.status_code >= 500:
                last_err = str(resp.status_code) + ": " + resp.text
                await asyncio.sleep(0.5 * (attempt + 1))
                continue
            body: Any
            try:
                body = resp.json() if resp.content else None
            except ValueError:
                body = resp.text
            clean_path = path.split("?", 1)[0]
            desc = _describe_fp_call(method, path)
            if resp.status_code >= 400:
                logger.warning(
                    "!!! FP-API !!! %s %s -> %d (%dms) :: %s [FAILED: %s]",
                    method,
                    clean_path,
                    resp.status_code,
                    dur_ms,
                    desc,
                    _short_fp_error(body),
                )
                raise FpError(
                    "FP "
                    + method
                    + " "
                    + path
                    + " -> "
                    + str(resp.status_code)
                    + ": "
                    + resp.text,
                    status_code=resp.status_code,
                    body=body,
                )
            logger.info(
                ">>> FP-API >>> %s %s -> %d (%dms) :: %s",
                method,
                clean_path,
                resp.status_code,
                dur_ms,
                desc,
            )
            return body
        raise FpError("FP " + method + " " + path + " exhausted retries: " + last_err)


def get_fp_client() -> FpClient:
    settings = get_settings()
    if not settings.fp_enabled():
        raise FpConfigError(
            "Fintech Primitives is not configured — set FP_TENANT, FP_API_KEY, FP_API_SECRET in .env."
        )
    return FpClient(
        base_url=settings.get_fp_base_url(),
        tenant=settings.get_fp_tenant() or "",
        client_id=settings.get_fp_api_key() or "",
        client_secret=settings.get_fp_api_secret() or "",
    )


def get_fp_preverify_client() -> FpClient:
    """Client for the Pre-Verification (KYC) service — same base host, but the
    ``cybrillarta`` tenant with its own client id/secret."""
    settings = get_settings()
    if not settings.fp_preverify_enabled():
        raise FpConfigError(
            "FP Pre-Verification (KYC) is not configured — set FP_PREVERIFY_CLIENT_ID and FP_PREVERIFY_CLIENT_SECRET in .env."
        )
    return FpClient(
        base_url=settings.get_fp_base_url(),
        tenant=settings.get_fp_preverify_tenant() or "",
        client_id=settings.get_fp_preverify_client_id() or "",
        client_secret=settings.get_fp_preverify_client_secret() or "",
    )
