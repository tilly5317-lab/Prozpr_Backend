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
    """Plain-text alternative. Not a nicety — some clients render only this,
    and a code-only mail with no text part scores badly with spam filters."""
    return (
        "Reset your Prozpr PIN\n"
        "=====================\n\n"
        f"Your reset code is: {code}\n\n"
        f"Enter it in the app to choose a new PIN. The code expires in "
        f"{minutes} minutes and can only be used once.\n\n"
        "Didn't ask for this?\n"
        "You can safely ignore this email. Your current PIN still works and "
        "nothing about your account has changed.\n\n"
        "--\n"
        "Prozpr - Wealth, Unified.\n"
        "This is an automated message; replies to it are not monitored.\n"
    )


def _html_body(code: str, minutes: int) -> str:
    """Branded HTML alternative.

    Written for mail clients, not browsers: a centred table rather than flex or
    grid, inline styles only (Gmail strips <style> blocks), no external images
    or web fonts (blocked by default), and a hidden preheader so the inbox
    preview shows something useful instead of the first stray words of markup.
    """
    return f"""\
<div style="display:none;max-height:0;overflow:hidden;opacity:0">
  Your Prozpr PIN reset code is {code} - it expires in {minutes} minutes.
</div>
<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"
       style="background-color:#f4f5f7;padding:32px 12px;
              font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,
              Helvetica,Arial,sans-serif">
  <tr>
    <td align="center">
      <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"
             style="max-width:480px;background-color:#ffffff;border-radius:14px;
                    border:1px solid #e4e6ea;overflow:hidden">
        <tr>
          <td style="padding:28px 32px 0 32px">
            <p style="margin:0;font-size:13px;font-weight:600;letter-spacing:1.4px;
                      text-transform:uppercase;color:#6b7280">Prozpr</p>
            <h1 style="margin:14px 0 0 0;font-size:21px;line-height:1.3;
                       font-weight:600;color:#111827">Reset your PIN</h1>
            <p style="margin:10px 0 0 0;font-size:14px;line-height:1.6;color:#4b5563">
              Enter this code in the app to choose a new sign-in PIN.
            </p>
          </td>
        </tr>
        <tr>
          <td style="padding:24px 32px">
            <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"
                   style="background-color:#f4f5f7;border-radius:10px">
              <tr>
                <td align="center" style="padding:22px 12px">
                  <span style="font-size:34px;font-weight:600;letter-spacing:10px;
                               color:#111827;font-family:'SFMono-Regular',Menlo,
                               Consolas,monospace">{code}</span>
                </td>
              </tr>
            </table>
            <p style="margin:16px 0 0 0;font-size:13px;line-height:1.6;color:#6b7280">
              This code expires in <strong style="color:#374151">{minutes} minutes</strong>
              and can only be used once.
            </p>
          </td>
        </tr>
        <tr>
          <td style="padding:0 32px 26px 32px">
            <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"
                   style="border-top:1px solid #e4e6ea">
              <tr>
                <td style="padding-top:18px">
                  <p style="margin:0;font-size:13px;font-weight:600;color:#374151">
                    Didn't ask for this?
                  </p>
                  <p style="margin:6px 0 0 0;font-size:13px;line-height:1.6;color:#6b7280">
                    You can safely ignore this email. Your current PIN still works
                    and nothing about your account has changed.
                  </p>
                </td>
              </tr>
            </table>
          </td>
        </tr>
      </table>
      <p style="margin:18px 0 0 0;font-size:11px;line-height:1.6;color:#9ca3af;
                max-width:480px">
        Prozpr - Wealth, Unified.<br>
        This is an automated message; replies to it are not monitored.
      </p>
    </td>
  </tr>
</table>"""


async def send_pin_reset_code(to_email: str, code: str, expires_in_minutes: int) -> None:
    """Email a reset code. Raises rather than returning a status so a delivery
    failure can never be mistaken for a delivered mail."""
    settings = get_settings()
    api_key = settings.get_resend_api_key()
    if not api_key:
        raise ResendNotConfigured("RESEND_API_KEY is not set")

    payload = {
        # Named sender — a bare address shows up as "support" in most inboxes.
        "from": f"{settings.get_resend_from_name()} <{settings.get_resend_from_email()}>",
        "to": [to_email],
        # The code is NOT in the subject: subjects show on lock screens and in
        # notification previews, where a shoulder-surfer would read it.
        "subject": "Reset your Prozpr PIN",
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
