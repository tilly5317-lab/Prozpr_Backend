"""Application service — `otp_service.py`.

Encapsulates business logic consumed by FastAPI routers. Uses database sessions, optional external APIs, and other services; should remain free of route-specific HTTP details (status codes live in routers).
"""


from __future__ import annotations

import logging
import os

import httpx

logger = logging.getLogger(__name__)

MSG91_BASE = "https://control.msg91.com/api/v5"
AUTH_KEY = os.getenv("MSG91_AUTH_KEY", "")
TEMPLATE_ID = os.getenv("MSG91_TEMPLATE_ID", "")


def _headers() -> dict[str, str]:
    return {"authkey": AUTH_KEY, "Content-Type": "application/json"}


def _mobile_international(country_code: str, mobile: str) -> str:
    """Return mobile in MSG91 format: country digits + mobile digits (no +)."""
    cc = "".join(c for c in country_code if c.isdigit())
    mob = "".join(c for c in mobile if c.isdigit())
    return cc + mob


async def send_otp(country_code: str, mobile: str, otp_length: int = 6) -> dict:
    number = _mobile_international(country_code, mobile)
    payload: dict = {"mobile": number, "otp_length": otp_length}
    if TEMPLATE_ID:
        payload["template_id"] = TEMPLATE_ID

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{MSG91_BASE}/otp",
            headers=_headers(),
            json=payload,
        )
    data = resp.json() if resp.status_code != 204 else {}
    logger.info("MSG91 send_otp %s -> %s %s", number, resp.status_code, data)
    if resp.status_code >= 400:
        raise RuntimeError(data.get("message", f"MSG91 error {resp.status_code}"))
    return data


async def verify_otp(country_code: str, mobile: str, otp: str) -> dict:
    number = _mobile_international(country_code, mobile)
    params = {"mobile": number, "otp": otp}

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"{MSG91_BASE}/otp/verify",
            headers=_headers(),
            params=params,
        )
    data = resp.json() if resp.status_code != 204 else {}
    logger.info("MSG91 verify_otp %s -> %s %s", number, resp.status_code, data)
    if resp.status_code >= 400:
        raise RuntimeError(data.get("message", f"MSG91 error {resp.status_code}"))
    return data


async def verify_widget_token(access_token: str) -> dict:
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{MSG91_BASE}/widget/verifyAccessToken",
            json={"authkey": AUTH_KEY, "access-token": access_token},
            headers={"Content-Type": "application/json"},
        )
    data = resp.json() if resp.status_code != 204 else {}
    logger.info("MSG91 verify_widget_token -> %s %s", resp.status_code, data)
    if resp.status_code >= 400:
        raise RuntimeError(data.get("message", f"MSG91 error {resp.status_code}"))
    return data


async def resend_otp(country_code: str, mobile: str, retry_type: str = "text") -> dict:
    number = _mobile_international(country_code, mobile)
    params = {"mobile": number, "retrytype": retry_type}

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"{MSG91_BASE}/otp/retry",
            headers=_headers(),
            params=params,
        )
    data = resp.json() if resp.status_code != 204 else {}
    logger.info("MSG91 resend_otp %s -> %s %s", number, resp.status_code, data)
    if resp.status_code >= 400:
        raise RuntimeError(data.get("message", f"MSG91 error {resp.status_code}"))
    return data
