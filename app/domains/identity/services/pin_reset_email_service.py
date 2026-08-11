"""Service — `pin_reset_email_service.py`.

Emails a forgot-PIN reset code through Resend's HTTP API.

Deliberately narrow: this is the ONLY thing Resend is used for. The support
domain keeps its own Zoho SMTP sender for issue reports, which posts to a fixed
internal inbox; reset codes are per-user mail and are kept on a separate sender
so one channel's reputation or rate limit can never take the other down.

Unlike the MSG91 OTP flow (where the provider generates AND verifies the code),
Resend only delivers a message — generating, hashing, expiring and checking the
code is the caller's job. See ``auth_router`` for that half.
"""

from __future__ import annotations

import logging

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_RESEND_ENDPOINT = "https://api.resend.com/emails"
_TIMEOUT_S = 10.0


class ResendNotConfigured(RuntimeError):
    """No API key — the caller must report the channel as unavailable rather
    than telling a user a mail is on its way that will never arrive."""


class ResendSendFailed(RuntimeError):
    """Resend accepted the request but rejected the message."""


def _plain_body(code: str, minutes: int) -> str:
    return (
        f"Your Prozpr PIN reset code is {code}.\n\n"
        f"It expires in {minutes} minutes and can be used once.\n\n"
        "If you didn't ask to reset your PIN, you can ignore this email — "
        "your current PIN still works and nothing has changed."
    )


def _html_body(code: str, minutes: int) -> str:
    # Inline styles only: mail clients strip <style> blocks, and this has to
    # stay readable in the ones that drop CSS entirely.
    return (
        '<div style="font-family:system-ui,-apple-system,Segoe UI,sans-serif;'
        'max-width:420px;color:#111">'
        '<p style="font-size:15px">Your Prozpr PIN reset code is:</p>'
        '<p style="font-size:30px;font-weight:600;letter-spacing:8px;'
        f'margin:20px 0">{code}</p>'
        f'<p style="font-size:13px;color:#555">It expires in {minutes} minutes '
        "and can be used once.</p>"
        '<p style="font-size:13px;color:#555">If you didn\'t ask to reset your '
        "PIN, you can ignore this email — your current PIN still works and "
        "nothing has changed.</p>"
        "</div>"
    )


async def send_pin_reset_code(to_email: str, code: str, expires_in_minutes: int) -> None:
    """Email a reset code. Raises rather than returning a status so a delivery
    failure can never be mistaken for a delivered mail."""
    settings = get_settings()
    api_key = settings.get_resend_api_key()
    if not api_key:
        raise ResendNotConfigured("RESEND_API_KEY is not set")

    payload = {
        "from": settings.get_resend_from_email(),
        "to": [to_email],
        "subject": "Your Prozpr PIN reset code",
        "text": _plain_body(code, expires_in_minutes),
        "html": _html_body(code, expires_in_minutes),
    }

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
            resp = await client.post(
                _RESEND_ENDPOINT,
                json=payload,
                headers={"Authorization": f"Bearer {api_key}"},
            )
    except httpx.HTTPError as exc:
        raise ResendSendFailed(f"Resend request failed: {exc}") from exc

    if resp.status_code >= 400:
        # Never log the body verbatim at info level — it echoes the recipient.
        logger.warning(
            "Resend rejected a PIN reset mail (status=%s)", resp.status_code
        )
        raise ResendSendFailed(
            f"Resend returned {resp.status_code}. A sending domain must be "
            "verified in Resend before mail can reach addresses other than the "
            "account owner's."
        )
