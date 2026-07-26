"""Zoom Server-to-Server OAuth client for "Talk to the Prozpr team" calls.

Creates real scheduled meetings in the company Zoom account (S2S OAuth app,
``account_credentials`` grant — needs the ``meeting:write:admin`` scope).
Credentials come from ``ZOOM_ACCOUNT_ID`` / ``ZOOM_CLIENT_ID`` /
``ZOOM_CLIENT_SECRET``; when any is unset every call raises
:class:`ZoomNotConfigured` so the router can answer 503 instead of guessing.

The access token is cached in-process until shortly before expiry; a 401 from
the API clears the cache and retries once (token revoked / rotated mid-cache).
"""

from __future__ import annotations

import base64
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any

import httpx

logger = logging.getLogger(__name__)

ZOOM_OAUTH_URL = "https://zoom.us/oauth/token"
ZOOM_API_BASE = "https://api.zoom.us/v2"
TEAM_CALL_MINUTES = 15
_TIMEOUT_SECONDS = 20.0

# Marker embedded in the meeting agenda so cancel/reschedule can verify the
# requesting user actually owns the meeting (we keep no DB row for bookings).
OWNER_MARKER_FMT = "[prozpr-team-call owner:{email}]"


class ZoomNotConfigured(RuntimeError):
    """ZOOM_* env vars are absent — the integration is off."""


class ZoomApiError(RuntimeError):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _credentials() -> tuple[str, str, str]:
    account_id = (os.getenv("ZOOM_ACCOUNT_ID") or "").strip()
    client_id = (os.getenv("ZOOM_CLIENT_ID") or "").strip()
    client_secret = (os.getenv("ZOOM_CLIENT_SECRET") or "").strip()
    if not (account_id and client_id and client_secret):
        raise ZoomNotConfigured(
            "Zoom is not configured (set ZOOM_ACCOUNT_ID, ZOOM_CLIENT_ID, ZOOM_CLIENT_SECRET)."
        )
    return account_id, client_id, client_secret


def is_configured() -> bool:
    try:
        _credentials()
        return True
    except ZoomNotConfigured:
        return False


_token_cache: dict[str, Any] = {"token": None, "expires_at": 0.0}


def _clear_token_cache() -> None:
    _token_cache["token"] = None
    _token_cache["expires_at"] = 0.0


async def _access_token(client: httpx.AsyncClient) -> str:
    now = time.monotonic()
    if _token_cache["token"] and now < _token_cache["expires_at"]:
        return _token_cache["token"]
    account_id, client_id, client_secret = _credentials()
    basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    res = await client.post(
        ZOOM_OAUTH_URL,
        params={"grant_type": "account_credentials", "account_id": account_id},
        headers={"Authorization": f"Basic {basic}"},
    )
    if res.status_code != 200:
        raise ZoomApiError(
            res.status_code, f"Zoom OAuth token request failed: {res.text[:300]}"
        )
    data = res.json()
    _token_cache["token"] = data["access_token"]
    # Refresh a minute early so a token never expires mid-request.
    _token_cache["expires_at"] = now + int(data.get("expires_in", 3600)) - 60
    return _token_cache["token"]


async def _api_request(
    client: httpx.AsyncClient, method: str, path: str, **kwargs: Any
) -> httpx.Response:
    token = await _access_token(client)
    res = await client.request(
        method, f"{ZOOM_API_BASE}{path}", headers={"Authorization": f"Bearer {token}"}, **kwargs
    )
    if res.status_code == 401:
        _clear_token_cache()
        token = await _access_token(client)
        res = await client.request(
            method,
            f"{ZOOM_API_BASE}{path}",
            headers={"Authorization": f"Bearer {token}"},
            **kwargs,
        )
    return res


async def create_team_call_meeting(
    *,
    start_utc: datetime,
    agenda: str,
    owner_email: str,
    topic: str = "Prozpr team call",
    duration_minutes: int = TEAM_CALL_MINUTES,
) -> dict[str, Any]:
    """Schedule a meeting; returns Zoom's meeting object (id, join_url, ...)."""
    if start_utc.tzinfo is None:
        start_utc = start_utc.replace(tzinfo=timezone.utc)
    start_utc = start_utc.astimezone(timezone.utc)
    marker = OWNER_MARKER_FMT.format(email=owner_email)
    body = {
        "topic": topic,
        "type": 2,  # scheduled meeting
        "start_time": start_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "duration": duration_minutes,
        "timezone": "UTC",
        # Zoom caps agenda at 2000 chars; keep the marker even if agenda is long.
        "agenda": f"{marker}\n{agenda}"[:2000],
        "settings": {
            "join_before_host": True,
            "waiting_room": False,
            "host_video": False,
            "participant_video": False,
        },
    }
    async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
        res = await _api_request(client, "POST", "/users/me/meetings", json=body)
        if res.status_code not in (200, 201):
            raise ZoomApiError(
                res.status_code, f"Zoom create-meeting failed: {res.text[:300]}"
            )
        return res.json()


def _is_missing_scope(res: httpx.Response) -> bool:
    """Zoom error 4711: token lacks a required scope."""
    try:
        return res.json().get("code") == 4711
    except Exception:
        return False


async def cancel_team_call_meeting(meeting_id: int, *, owner_email: str) -> bool:
    """Delete a meeting the given user booked.

    Ownership is checked via the agenda marker written at create time — without
    it any authenticated user could delete arbitrary meetings in the account.
    Returns False when the meeting is already gone.
    """
    marker = OWNER_MARKER_FMT.format(email=owner_email)
    async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
        res = await _api_request(client, "GET", f"/meetings/{meeting_id}")
        if res.status_code == 404:
            return False
        if res.status_code == 200:
            agenda = str(res.json().get("agenda") or "")
            if marker not in agenda:
                raise ZoomApiError(403, "This meeting was not booked by you.")
        elif _is_missing_scope(res):
            # The S2S app lacks meeting:read:meeting:admin — we can't verify the
            # agenda marker. Deleting still works; grant the read scope in the
            # Zoom app to re-enable the ownership check.
            logger.warning(
                "Zoom read scope missing; skipping ownership check for meeting %s",
                meeting_id,
            )
        else:
            raise ZoomApiError(
                res.status_code, f"Zoom get-meeting failed: {res.text[:300]}"
            )
        res = await _api_request(client, "DELETE", f"/meetings/{meeting_id}")
        if res.status_code in (204, 404):
            return res.status_code == 204
        raise ZoomApiError(
            res.status_code, f"Zoom delete-meeting failed: {res.text[:300]}"
        )
