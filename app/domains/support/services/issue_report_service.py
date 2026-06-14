"""Issue-report storage + notification: Google Sheet register, screenshots, email.

There is deliberately NO database table for issue reports. The shared Google
Sheet IS the one and only issue register (no local fallback):

- ``ISSUE_SHEET_WEBHOOK_URL`` (.env) — a Google Apps Script web app attached to
  the admin's Google Sheet; we POST one row per report:
  Date | User Name | Email | Source | Issue | Screenshot | Remarks
  (Remarks stays empty — it is the admin's column to fill while triaging.)
  ``ISSUE_SHEET_TOKEN`` is a shared secret the script checks so strangers
  cannot post junk rows if the URL leaks.
  Because the Sheet is the sole register, ``append_to_google_sheet`` raises on
  every failure mode (webhook unset, network error, script rejection) so the
  router answers 503 and the user is asked to retry — a report is never lost
  to a silent success.
- ``issue_reports/screenshots/<report_id>.<ext>`` — optional screenshot file
  (also attached to the email).
- Email to ``SUPPORT_EMAIL_TO`` via Zoho SMTP (``smtp.zoho.com:465`` SSL).
  Skipped with a log warning when ``SMTP_PASSWORD`` is not configured — a
  report must never fail because mail is down.

All functions are synchronous on purpose: the router runs the register POST /
screenshot save via ``asyncio.to_thread`` and the email as a background task.
"""

from __future__ import annotations

import logging
import smtplib
import ssl
import time
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)

try:
    from zoneinfo import ZoneInfo

    IST = ZoneInfo("Asia/Kolkata")
except Exception:  # Windows without the `tzdata` package — fixed offset is exact for IST.
    IST = timezone(timedelta(hours=5, minutes=30), "IST")

# Prozpr_Backend/issue_reports/ — alongside app/, not inside it. Only screenshots
# live here now; the register itself is the remote Google Sheet.
_REPORTS_DIR = Path(__file__).resolve().parents[4] / "issue_reports"
SCREENSHOTS_DIR = _REPORTS_DIR / "screenshots"

# Column order one row maps to — kept in sync with the Apps Script's HEADERS.
SHEET_HEADERS = ["Date", "User Name", "Email", "Source", "Issue", "Screenshot", "Remarks"]

ALLOWED_SOURCES = {
    "Chat Response",
    "Portfolio NAV",
    "Rebalancing",
    "Goal Planning",
    "Onboarding",
    "Other",
}

ALLOWED_IMAGE_TYPES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
}
MAX_SCREENSHOT_BYTES = 5 * 1024 * 1024  # 5 MB

# Transient webhook failures (network blips, Apps Script cold starts) are retried;
# a script-level rejection (bad token / ok != true) or a 4xx is not — those are
# deterministic and retrying only delays a report we already know failed.
_WEBHOOK_TIMEOUT_SECONDS = 20
_WEBHOOK_MAX_ATTEMPTS = 3
_WEBHOOK_RETRY_BACKOFF_SECONDS = 1.5


def append_to_google_sheet(
    created_at: datetime,
    user_name: str,
    user_email: str,
    source_label: str,
    description: str,
    screenshot_name: str | None,
) -> None:
    """POST one register row to the Apps Script webhook — the sole issue register.

    Returns ``None`` on success and raises on every failure (webhook unset,
    network/HTTP error, or a script that did not answer ``{"ok": true}``). The
    router turns any exception into a 503, so a return here guarantees the row
    is in the Sheet.
    """
    settings = get_settings()
    url = settings.get_issue_sheet_webhook_url()
    if not url:
        raise RuntimeError(
            "ISSUE_SHEET_WEBHOOK_URL is not configured — cannot record the issue report."
        )

    payload = {
        "token": settings.get_issue_sheet_token() or "",
        "date": created_at.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S"),
        "user_name": user_name,
        "email": user_email,
        "source": source_label,
        "issue": description,
        "screenshot": screenshot_name or "",
    }

    last_exc: Exception | None = None
    for attempt in range(1, _WEBHOOK_MAX_ATTEMPTS + 1):
        try:
            # Apps Script answers with a 302 to script.googleusercontent.com — follow it.
            resp = httpx.post(
                url, json=payload, timeout=_WEBHOOK_TIMEOUT_SECONDS, follow_redirects=True
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            # 5xx can be a transient Apps Script cold start; 4xx is a config error.
            if status >= 500 and attempt < _WEBHOOK_MAX_ATTEMPTS:
                last_exc = exc
                logger.warning(
                    "Google Sheet webhook attempt %d/%d failed (HTTP %s); retrying.",
                    attempt, _WEBHOOK_MAX_ATTEMPTS, status,
                )
                time.sleep(_WEBHOOK_RETRY_BACKOFF_SECONDS * attempt)
                continue
            raise RuntimeError(
                f"Issue sheet webhook HTTP {status}: {exc.response.text[:200]}"
            ) from exc
        except httpx.RequestError as exc:
            # Connection refused / DNS / timeout — transient; retry then give up.
            last_exc = exc
            if attempt < _WEBHOOK_MAX_ATTEMPTS:
                logger.warning(
                    "Google Sheet webhook attempt %d/%d failed (%s); retrying.",
                    attempt, _WEBHOOK_MAX_ATTEMPTS, exc,
                )
                time.sleep(_WEBHOOK_RETRY_BACKOFF_SECONDS * attempt)
                continue
            raise RuntimeError(
                f"Issue sheet webhook unreachable after {_WEBHOOK_MAX_ATTEMPTS} attempts: {exc}"
            ) from exc

        # 2xx — validate the script's JSON contract. Deterministic, so not retried.
        try:
            body = resp.json()
        except ValueError as exc:
            raise RuntimeError(
                f"Issue sheet webhook returned non-JSON: {resp.text[:200]}"
            ) from exc
        if body.get("ok") is not True:
            raise RuntimeError(f"Issue sheet webhook rejected the row: {body}")

        logger.info(
            "Issue report recorded to Google Sheet (source=%s, user=%s).",
            source_label,
            user_email or "-",
        )
        return

    # Loop only falls through here after exhausting retries on transient errors.
    raise RuntimeError(
        f"Issue sheet webhook failed after {_WEBHOOK_MAX_ATTEMPTS} attempts"
    ) from last_exc


def save_screenshot(content: bytes, content_type: str, report_id: str) -> str:
    """Persist screenshot bytes; returns the stored path as a string."""
    ext = ALLOWED_IMAGE_TYPES[content_type]
    SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    path = SCREENSHOTS_DIR / f"{report_id}{ext}"
    path.write_bytes(content)
    return str(path)


def send_issue_email(
    created_at: datetime,
    user_name: str,
    user_email: str,
    source_label: str,
    description: str,
    screenshot: bytes | None,
    screenshot_type: str | None,
) -> None:
    """Email the report to the support inbox; failures are logged, never raised."""
    settings = get_settings()
    password = settings.get_smtp_password()
    if not password:
        logger.warning(
            "SMTP_PASSWORD not set — issue report email skipped (report is in the Google Sheet)."
        )
        return

    sender = settings.get_smtp_user()
    recipient = settings.get_support_email_to()
    when = created_at.astimezone(IST).strftime("%d %b %Y, %I:%M %p IST")

    msg = EmailMessage()
    msg["Subject"] = f"[Prozpr Issue] {source_label} — {user_name or user_email or 'Unknown user'}"
    msg["From"] = sender
    msg["To"] = recipient
    msg.set_content(
        f"A user reported an issue on Prozpr.\n"
        f"\n"
        f"Date       : {when}\n"
        f"User Name  : {user_name or '-'}\n"
        f"Email      : {user_email or '-'}\n"
        f"Source     : {source_label}\n"
        f"\n"
        f"Issue:\n{description}\n"
        f"\n"
        f"Screenshot : {'attached' if screenshot else 'not provided'}\n"
    )

    if screenshot and screenshot_type:
        maintype, _, subtype = screenshot_type.partition("/")
        ext = ALLOWED_IMAGE_TYPES.get(screenshot_type, ".png")
        msg.add_attachment(
            screenshot, maintype=maintype, subtype=subtype, filename=f"screenshot{ext}"
        )

    host = settings.get_smtp_host()
    port = settings.get_smtp_port()
    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, context=ssl.create_default_context(), timeout=30) as smtp:
                smtp.login(sender, password)
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=30) as smtp:
                smtp.starttls(context=ssl.create_default_context())
                smtp.login(sender, password)
                smtp.send_message(msg)
        logger.info("Issue report email sent to %s (source=%s)", recipient, source_label)
    except Exception:
        logger.exception("Failed to send issue report email (report is in the Google Sheet).")
