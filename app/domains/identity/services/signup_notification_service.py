"""New-signup team notification: Slack ping + optional Google Sheet row.

When a user submits the name / PIN / email setup page — i.e. the first time
their identity (name + email) becomes complete, on `/auth/signup` (or the
`/auth/me` safety-net call the setup page makes) — we let the team know a new
person signed up, right away, without building an admin dashboard. Firing at
this point (rather than at `/onboarding/complete`) means the team hears about a
signup even if the user abandons before uploading their CAMS statement:

- **Slack** (``SLACK_SIGNUP_WEBHOOK_URL``) — a Slack Incoming Webhook that posts
  one message per signup to a team channel (e.g. ``#signups``). This is the
  primary "everyone gets notified" channel, and the channel itself doubles as a
  searchable running log of every signup.
- **Google Sheet** (``SIGNUP_SHEET_WEBHOOK_URL``, optional) — a Google Apps
  Script web app, the same pattern as the issue register
  (``issue_report_service``). We POST one row per signup so the team also has a
  sortable "all users in one place" table. ``SIGNUP_SHEET_TOKEN`` is a shared
  secret the script checks so strangers cannot post junk rows if the URL leaks.

Both channels are **best-effort**: every failure is logged, never raised, and an
unset webhook is skipped. A signup must never fail because a notification
channel is down — the router schedules ``notify_new_signup`` as a FastAPI
background task that runs after the 201 response is sent.

Synchronous on purpose (blocking ``httpx.post``): FastAPI runs a sync background
task in its threadpool, so this never blocks the event loop.
"""

from __future__ import annotations

import logging
import os
import ssl
from datetime import datetime, timedelta, timezone
from functools import lru_cache

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _ssl_context() -> ssl.SSLContext:
    """A fully-verifying TLS context that does NOT depend on certifi's bundled
    ``cacert.pem`` file.

    ``ssl.create_default_context()`` seeds the trust store from the operating
    system (the Windows cert store / the system CA directory on Linux), so
    outbound HTTPS keeps working even when the virtualenv's ``certifi`` package
    is missing its data file — the exact failure that once silently dropped
    these signup pings (``httpx`` defaults ``verify=`` to ``certifi.where()``,
    which raised ``FileNotFoundError`` when that file was absent). Certifi's
    bundle, when present, is loaded too as a belt-and-suspenders extra."""
    ctx = ssl.create_default_context()
    try:
        import certifi

        ca = certifi.where()
        if os.path.exists(ca):
            ctx.load_verify_locations(cafile=ca)
    except Exception:
        # No certifi / unreadable bundle — the OS trust store already loaded
        # above is sufficient to verify public CAs like Slack's.
        pass
    return ctx

try:
    from zoneinfo import ZoneInfo

    IST = ZoneInfo("Asia/Kolkata")
except (
    Exception
):  # Windows without the `tzdata` package — fixed offset is exact for IST.
    IST = timezone(timedelta(hours=5, minutes=30), "IST")

# Column order one signup row maps to — keep in sync with the Apps Script HEADERS.
SIGNUP_SHEET_HEADERS = ["Date", "Name", "Email", "Phone", "Source"]

# Notifications run in a background task (after the response), so a generous
# timeout is fine; it never holds up the user's signup.
_TIMEOUT_SECONDS = 8


def _post_to_slack(text: str) -> None:
    """POST a message to the Slack Incoming Webhook. Skipped/ logged, never raised."""
    settings = get_settings()
    url = settings.get_slack_signup_webhook_url()
    if not url:
        logger.debug("SLACK_SIGNUP_WEBHOOK_URL not set — Slack signup ping skipped.")
        return
    try:
        resp = httpx.post(
            url,
            json={"text": text},
            timeout=_TIMEOUT_SECONDS,
            verify=_ssl_context(),
        )
        resp.raise_for_status()
        logger.info("New-signup notification posted to Slack.")
    except Exception:
        logger.exception("Failed to post new-signup notification to Slack.")


def _append_to_signup_sheet(
    created_at: datetime, name: str, email: str, phone: str, source: str
) -> None:
    """Append one signup row to the optional Google Sheet. Logged, never raised."""
    settings = get_settings()
    url = settings.get_signup_sheet_webhook_url()
    if not url:
        return  # The Sheet is optional; Slack is the primary channel.
    payload = {
        "token": settings.get_signup_sheet_token() or "",
        "date": created_at.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S"),
        "name": name,
        "email": email,
        "phone": phone,
        "source": source,
    }
    try:
        # Apps Script answers with a 302 to script.googleusercontent.com — follow it.
        resp = httpx.post(
            url,
            json=payload,
            timeout=_TIMEOUT_SECONDS,
            follow_redirects=True,
            verify=_ssl_context(),
        )
        resp.raise_for_status()
        logger.info("New signup appended to the Google Sheet.")
    except Exception:
        logger.exception("Failed to append new signup to the Google Sheet.")


def notify_new_signup(
    created_at: datetime,
    name: str,
    email: str,
    phone: str,
    source: str = "Signup",
) -> None:
    """Best-effort team notification of a brand-new signup. Never raises.

    Fans out to every configured channel (Slack, optional Google Sheet). An
    unconfigured channel is silently skipped, so the team can start with Slack
    alone and add the Sheet later with no code change.
    """
    when = created_at.astimezone(IST).strftime("%d %b %Y, %I:%M %p IST")
    display_name = name or "(name pending)"
    text = (
        ":wave: *New signup on Prozpr*\n"
        f"*Name:* {display_name}\n"
        f"*Phone:* {phone or '-'}\n"
        f"*Email:* {email or '-'}\n"
        f"*When:* {when}"
    )
    _post_to_slack(text)
    _append_to_signup_sheet(created_at, display_name, email, phone, source)
