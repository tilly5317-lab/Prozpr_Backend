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
import uuid

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
    and a code-only mail with no text part scores badly with spam filters.

    The code appears on its own line in the `Code: NNNNNN` shape iOS and
    Android scan for when offering to autofill a one-time code, so a user who
    reads this part still gets the keyboard suggestion.
    """
    return (
        "Reset your Prozpr PIN\n"
        "=====================\n\n"
        f"Code: {code}\n\n"
        f"Enter it in the app to choose a new PIN. It expires in {minutes} "
        "minutes and can only be used once.\n\n"
        "Prozpr will never ask you for this code by phone, email or chat.\n\n"
        "Didn't ask for this?\n"
        "You can safely ignore this email. Your current PIN still works and "
        "nothing about your account has changed.\n\n"
        "--\n"
        "Prozpr - Wealth, Unified.\n"
        "This is an automated message; replies to it are not monitored.\n"
    )


"""Branded HTML alternative.

Written for mail clients, not browsers: centred tables rather than flex or grid,
inline styles carrying the whole light-mode design (Outlook and some Gmail paths
drop <style> blocks, so nothing load-bearing may live there), and no external
images or web fonts — both are blocked by default, and a design that needs them
arrives broken.

A plain string with `{{CODE}}` / `{{MINUTES}}` placeholders rather than an
f-string: the CSS block is mostly braces, and escaping every one of them for the
formatter makes the markup unreadable.

Decisions worth keeping:

- **The code is ONE contiguous text run**, not one box per digit. Boxed digits
  look closer to the app's OTP input, but they defeat both copy-paste (the
  selection comes out with spaces) and the iOS/Android one-time-code autofill
  scan, which needs the digits adjacent. Letter-spacing buys the same legibility
  for free — and `letter-spacing` on the last character pads the right side, so
  the span is nudged back by half of it to stay optically centred.
- **The code is NOT in the preheader.** The preheader is what shows in the inbox
  list and the lock-screen notification — the same shoulder-surfing surface the
  subject deliberately avoids, so leaving the code out of one and not the other
  would be pointless.
- **Dark mode is a `@media (prefers-color-scheme: dark)` block of `!important`
  overrides**, since it has to beat the inline styles. Clients that ignore the
  block (Outlook) keep the light design, which is why light lives inline.
- **An anti-phishing line.** Reset-code mail is the single most impersonated
  message a fintech sends; saying plainly that nobody from Prozpr will ask for
  the code is the cheapest defence there is.
"""
_HTML_TEMPLATE = """\
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light dark">
<meta name="supported-color-schemes" content="light dark">
<title>Reset your Prozpr PIN</title>
<style>
  @media only screen and (max-width:440px) {
    .px-outer { padding:20px 10px !important; }
    .px-card  { padding:26px 22px !important; }
    .px-code  { font-size:27px !important; letter-spacing:7px !important; }
  }
  @media (prefers-color-scheme:dark) {
    .px-page   { background-color:#0f1115 !important; }
    .px-card   { background-color:#171a21 !important; border-color:#262b34 !important; }
    .px-brand  { color:#8f98a6 !important; }
    .px-head   { color:#f0efe9 !important; }
    .px-body   { color:#c3c8d1 !important; }
    .px-muted  { color:#9aa1ac !important; }
    .px-chip   { background-color:#1e232c !important; border-color:#2f3641 !important; }
    .px-code   { color:#f0efe9 !important; }
    .px-strong { color:#e6e8ec !important; }
    .px-rule   { border-color:#262b34 !important; }
    .px-foot   { color:#767d88 !important; }
  }
</style>
</head>
<body class="px-page" style="margin:0;padding:0;width:100%;background-color:#f4f5f7;">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;mso-hide:all" aria-hidden="true">
  Your PIN reset code is inside, and expires in {{MINUTES}} minutes.
</div>
<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"
       class="px-page" style="background-color:#f4f5f7">
  <tr>
    <td align="center" class="px-outer" style="padding:36px 12px">
      <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"
             class="px-card"
             style="max-width:480px;background-color:#ffffff;border-radius:16px;
                    border:1px solid #e4e6ea;padding:32px 34px;
                    font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,
                    Helvetica,Arial,sans-serif">
        <tr>
          <td>
            <p class="px-brand" style="margin:0;font-size:12px;font-weight:700;
                      letter-spacing:1.6px;text-transform:uppercase;color:#6b7280">Prozpr</p>
            <h1 class="px-head" style="margin:16px 0 0 0;font-size:22px;line-height:1.3;
                       font-weight:600;color:#131d34">Reset your PIN</h1>
            <p class="px-body" style="margin:10px 0 0 0;font-size:15px;line-height:1.6;color:#4b5563">
              Enter this code in the app to choose a new sign-in PIN.
            </p>
          </td>
        </tr>
        <tr>
          <td style="padding:22px 0 0 0">
            <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"
                   class="px-chip"
                   style="background-color:#f4f6fa;border:1px solid #dce3ee;border-radius:12px">
              <tr>
                <td align="center" style="padding:24px 10px">
                  <!-- One run of digits, kept adjacent for copy-paste and OTP autofill. -->
                  <span class="px-code"
                        style="display:inline-block;font-size:32px;font-weight:600;
                               letter-spacing:9px;margin-right:-9px;color:#131d34;
                               font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,
                               'Liberation Mono',monospace">{{CODE}}</span>
                </td>
              </tr>
            </table>
            <p class="px-muted" style="margin:14px 0 0 0;font-size:13px;line-height:1.6;color:#6b7280">
              Expires in <strong class="px-strong" style="color:#374151">{{MINUTES}} minutes</strong>,
              and works only once.
            </p>
          </td>
        </tr>
        <tr>
          <td style="padding:22px 0 0 0">
            <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"
                   class="px-rule" style="border-top:1px solid #e4e6ea">
              <tr>
                <td style="padding-top:20px">
                  <p class="px-strong" style="margin:0;font-size:13px;font-weight:600;color:#374151">
                    Didn't ask for this?
                  </p>
                  <p class="px-muted" style="margin:6px 0 0 0;font-size:13px;line-height:1.6;color:#6b7280">
                    You can safely ignore this email. Your current PIN still works
                    and nothing about your account has changed.
                  </p>
                  <p class="px-muted" style="margin:14px 0 0 0;font-size:13px;line-height:1.6;color:#6b7280">
                    Prozpr will never ask you for this code by phone, email or chat.
                  </p>
                </td>
              </tr>
            </table>
          </td>
        </tr>
      </table>
      <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"
             style="max-width:480px">
        <tr>
          <td style="padding:18px 6px 0 6px">
            <p class="px-foot" style="margin:0;font-size:11px;line-height:1.7;color:#9ca3af;
                      font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,
                      Helvetica,Arial,sans-serif">
              Prozpr - Wealth, Unified.<br>
              This is an automated message; replies to it are not monitored.
            </p>
          </td>
        </tr>
      </table>
    </td>
  </tr>
</table>
</body>
</html>"""


def _html_body(code: str, minutes: int) -> str:
    """Fill the template. `code` is digits and `minutes` an int, so neither
    needs HTML-escaping — keep it that way if either ever becomes caller text."""
    return _HTML_TEMPLATE.replace("{{CODE}}", code).replace("{{MINUTES}}", str(minutes))


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
        # Unique per send, so Gmail treats each code as its own message instead
        # of collapsing a run of them into one thread — where the newest sits
        # collapsed under the oldest and users read a code that no longer works.
        "headers": {"X-Entity-Ref-ID": uuid.uuid4().hex},
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
