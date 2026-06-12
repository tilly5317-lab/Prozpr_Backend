"""Issue-report side effects: screenshot storage and the support email.

The DB row (``IssueReport``) is the source of truth and is written by the
router inside the request. Everything here is a derived artifact:

- ``issue_reports/screenshots/<report_id>.<ext>`` — optional screenshot.
- Email to ``SUPPORT_EMAIL_TO`` via Zoho SMTP (``smtp.zoho.com:465`` SSL),
  screenshot attached. Skipped with a log warning when ``SMTP_PASSWORD`` is
  not configured — a report must never fail because mail is down.

All functions are synchronous on purpose: the router runs the email as a
FastAPI background task and the screenshot save via ``asyncio.to_thread``.
"""

from __future__ import annotations

import logging
import smtplib
import ssl
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path

from app.core.config import get_settings

logger = logging.getLogger(__name__)

try:
    from zoneinfo import ZoneInfo

    IST = ZoneInfo("Asia/Kolkata")
except Exception:  # Windows without the `tzdata` package — fixed offset is exact for IST.
    IST = timezone(timedelta(hours=5, minutes=30), "IST")

# Prozpr_Backend/issue_reports/ — alongside app/, not inside it.
_REPORTS_DIR = Path(__file__).resolve().parents[4] / "issue_reports"
SCREENSHOTS_DIR = _REPORTS_DIR / "screenshots"

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
    source: str,
    source_detail: str | None,
    description: str,
    screenshot: bytes | None,
    screenshot_type: str | None,
) -> None:
    """Email the report to the support inbox; failures are logged, never raised."""
    settings = get_settings()
    password = settings.get_smtp_password()
    if not password:
        logger.warning(
            "SMTP_PASSWORD not set — issue report email skipped (report is in the DB)."
        )
        return

    sender = settings.get_smtp_user()
    recipient = settings.get_support_email_to()
    when = created_at.astimezone(IST).strftime("%d %b %Y, %I:%M %p IST")
    source_label = f"{source} — {source_detail}" if source_detail else source

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
        logger.info("Issue report email sent to %s (source=%s)", recipient, source)
    except Exception:
        logger.exception("Failed to send issue report email (report is in the DB).")
