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
            logger.info("FP tenant token minted (ttl~%.0fs)", ttl)
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
            try:
                resp = await self._http.request(
                    method, url, headers=headers, json=json, params=params
                )
            except httpx.HTTPError as exc:
                last_err = str(exc)
                await asyncio.sleep(0.5 * (attempt + 1))
                continue
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
            if resp.status_code >= 400:
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
