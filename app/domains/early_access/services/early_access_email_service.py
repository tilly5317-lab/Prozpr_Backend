"""Confirmation mail for a new early-access application (Resend).

Without this, an applicant fills the form, sees a success screen, and then
hears nothing — no record in their inbox that they applied, and nothing to
recognise when the invite finally arrives. This sends one personalised HTML
mail the moment their row lands: who they are, which seat they took, and what
happens next.

Sent through Resend (``RESEND_API_KEY``), the same channel as the forgot-PIN
code, from the verified ``notifications.prozpr.com`` sender. The template
follows ``identity/services/pin_reset_email_service`` deliberately — table
layout, inline styles, a dark-mode block — because that is what already renders
correctly across the clients Prozpr sends to, and a second house style would
just be a second thing to keep working.

**Best-effort, always.** Every failure is logged and swallowed: the Google
Sheet row is the thing that matters, and an applicant must never see their
application fail because a mail provider was down. The router fires this as a
background task, after the response.

Sent only for a NEW row, never on a repeat submission of the same address —
the second mail would say "you're in" to someone who already got that, and
read like a duplicate application.
"""

from __future__ import annotations

import html
import logging
import uuid

import httpx

from app.core.config import get_settings
from app.core.pii import mask_email

logger = logging.getLogger(__name__)

_RESEND_ENDPOINT = "https://api.resend.com/emails"
_TIMEOUT_S = 10.0


def _first_name(full_name: str) -> str:
    """The name to greet by. Falls back to a neutral greeting rather than an
    empty one — "Hi ," reads worse than no name at all."""
    first = (full_name or "").strip().split(" ")[0].strip()
    return first if first else "there"


_HTML_TEMPLATE = """\
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light dark">
<meta name="supported-color-schemes" content="light dark">
<title>{{TITLE}}</title>
<style>
  @media only screen and (max-width:440px) {
    .px-outer { padding:20px 10px !important; }
    .px-card  { padding:26px 22px !important; }
    .px-seat  { font-size:26px !important; }
  }
  @media (prefers-color-scheme:dark) {
    .px-page   { background-color:#0f1115 !important; }
    .px-card   { background-color:#171a21 !important; border-color:#262b34 !important; }
    .px-mark   { color:#f0efe9 !important; }
    .px-head   { color:#f0efe9 !important; }
    .px-body   { color:#c3c8d1 !important; }
    .px-muted  { color:#9aa1ac !important; }
    .px-chip   { background-color:#1e232c !important; border-color:#2f3641 !important; }
    .px-seat   { color:#f0efe9 !important; }
    .px-strong { color:#e6e8ec !important; }
    .px-rule   { border-color:#262b34 !important; }
    .px-foot   { color:#767d88 !important; }
  }
</style>
</head>
<body class="px-page" style="margin:0;padding:0;width:100%;background-color:#f4f5f7;">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;mso-hide:all" aria-hidden="true">
  {{PREHEADER}}
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
            <!-- The same wordmark the /earlyaccess navbar shows, set as TEXT
                 rather than an image. Most clients block remote images by
                 default, so a logo file would leave a blank box for a large
                 share of recipients; live text always renders. Instrument
                 Serif is the site face, with Georgia as the near-universal
                 email fallback (both carry the rupee glyph). -->
            <p class="px-mark" style="margin:0;font-size:26px;line-height:1;
                      color:#131d34;font-family:'Instrument Serif',Georgia,
                      'Times New Roman',serif">prozp&#8377;<span
                      style="color:#E0B84A">.</span></p>
            <h1 class="px-head" style="margin:16px 0 0 0;font-size:22px;line-height:1.3;
                       font-weight:600;color:#131d34">{{HEADING}}</h1>
            <p class="px-body" style="margin:10px 0 0 0;font-size:15px;line-height:1.6;color:#4b5563">
              {{INTRO}}
            </p>
          </td>
        </tr>
        <tr>
          <td style="padding:22px 0 0 0">
            <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"
                   class="px-chip"
                   style="background-color:#f4f6fa;border:1px solid #dce3ee;border-radius:12px">
              <tr>
                <td align="center" style="padding:22px 10px">
                  <p class="px-muted" style="margin:0;font-size:12px;font-weight:600;
                            letter-spacing:1.2px;text-transform:uppercase;color:#6b7280">
                    {{SEAT_LABEL}}
                  </p>
                  <p class="px-seat" style="margin:6px 0 0 0;font-size:30px;font-weight:600;
                            line-height:1.2;color:#131d34">{{SEAT_VALUE}}</p>
                </td>
              </tr>
            </table>
          </td>
        </tr>
        <tr>
          <td style="padding:22px 0 0 0">
            <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"
                   class="px-rule" style="border-top:1px solid #e4e6ea">
              <tr>
                <td style="padding-top:20px">
                  <p class="px-strong" style="margin:0;font-size:13px;font-weight:600;color:#374151">
                    What happens next
                  </p>
                  <p class="px-muted" style="margin:6px 0 0 0;font-size:13px;line-height:1.6;color:#6b7280">
                    {{NEXT}}
                  </p>
                  <p class="px-muted" style="margin:14px 0 0 0;font-size:13px;line-height:1.6;color:#6b7280">
                    The beta is free, and Prozpr will never ask you for payment
                    details or a one-time password.
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
              Prozpr Private Limited - Educational insights, not investment advice.
              AMFI and SEBI registration in process.<br>
              You are receiving this because you requested early access at
              {{SITE_HOST}}. This is an automated message; replies to it are not
              monitored.
            </p>
          </td>
        </tr>
      </table>
    </td>
  </tr>
</table>
</body>
</html>"""


def _render(
    *, first_name: str, seat: int, seats_total: int, waitlisted: bool
) -> tuple[str, str, str]:
    """Build (subject, text, html) for one applicant.

    Every interpolated value is either an int we produced or an HTML-escaped
    string. The name comes from a public form, so dropping it in raw would let
    a submitted name inject markup into a mail we send under our own domain.
    """
    # Two forms on purpose: the HTML part needs the escaped name, the plain
    # text part needs the real one. Reusing the escaped string for both is how
    # "O'Brien" reaches an inbox as "O&#x27;Brien".
    safe_name = html.escape(first_name)
    host = get_settings().get_public_site_url().split("://", 1)[-1].rstrip("/")

    if waitlisted:
        subject = "You are on the Prozpr early-access waitlist"
        preheader = (
            f"All {seats_total} beta seats are taken - you have the next place in line."
        )
        heading = f"You are on the waitlist, {safe_name}."
        heading_text = f"You are on the waitlist, {first_name}."
        intro = (
            f"Thank you for asking to test Prozpr MVP 2.0. All {seats_total} seats "
            "were claimed before your request arrived, so you have the next place "
            "in line rather than a seat."
        )
        seat_label = "Waitlist position"
        seat_value = f"#{seat - seats_total}" if seat > seats_total else "Next in line"
        next_line = (
            "Seats are confirmed in sign-up order, and testers do drop out. "
            "If one opens up we will email you before anyone else, and there is "
            "nothing you need to do in the meantime."
        )
        text_seat = f"Waitlist position: {seat_value}"
    else:
        subject = "You are in - your Prozpr beta seat is confirmed"
        preheader = f"Seat {seat} of {seats_total} is yours. Here is what happens next."
        heading = f"You are in, {safe_name}."
        heading_text = f"You are in, {first_name}."
        intro = (
            "Thank you for putting your hand up. We are glad to have you testing "
            "Prozpr MVP 2.0 before anyone else, and your seat is confirmed."
        )
        seat_label = "Your seat"
        seat_value = f"{seat} of {seats_total}"
        next_line = (
            "We will email your invite and the tester WhatsApp group link before "
            "the beta opens. Seats are confirmed in sign-up order, so nothing "
            "can take yours - there is nothing you need to do until then."
        )
        text_seat = f"Your seat: {seat} of {seats_total}"

    html_body = (
        _HTML_TEMPLATE.replace("{{TITLE}}", html.escape(subject))
        .replace("{{PREHEADER}}", html.escape(preheader))
        .replace("{{HEADING}}", heading)
        .replace("{{INTRO}}", html.escape(intro))
        .replace("{{SEAT_LABEL}}", html.escape(seat_label))
        .replace("{{SEAT_VALUE}}", html.escape(seat_value))
        .replace("{{NEXT}}", html.escape(next_line))
        .replace("{{SITE_HOST}}", html.escape(host))
    )

    # Plain text is not a formality: some clients show it, and spam filters
    # score a missing text part against the message.
    text_body = "\n".join(
        [
            heading_text,
            "",
            intro,
            "",
            text_seat,
            "",
            "What happens next",
            next_line,
            "",
            "The beta is free, and Prozpr will never ask you for payment details "
            "or a one-time password.",
            "",
            f"You are receiving this because you requested early access at {host}.",
            "This is an automated message; replies to it are not monitored.",
        ]
    )
    return subject, text_body, html_body


async def send_early_access_confirmation(
    *,
    to_email: str,
    full_name: str,
    seat: int,
    seats_total: int,
    waitlisted: bool,
) -> None:
    """Send one confirmation mail. Logs every failure; never raises.

    The applicant's row is already in the register by the time this runs, so
    there is nothing a failure here should undo — losing the mail is a smaller
    problem than losing the lead, and the two must not be coupled.
    """
    settings = get_settings()
    api_key = settings.get_resend_api_key()
    if not api_key:
        logger.info(
            "RESEND_API_KEY not set - early-access confirmation mail skipped for %s.",
            mask_email(to_email) or "-",
        )
        return

    subject, text_body, html_body = _render(
        first_name=_first_name(full_name),
        seat=seat,
        seats_total=seats_total,
        waitlisted=waitlisted,
    )
    payload = {
        # Named sender — a bare address shows up as "no-reply" in most inboxes.
        "from": f"{settings.get_resend_from_name()} <{settings.get_resend_from_email()}>",
        "to": [to_email],
        "subject": subject,
        "text": text_body,
        "html": html_body,
        # Unique per send, so a resend is never collapsed into an existing
        # Gmail thread where the newest message hides under the oldest.
        "headers": {"X-Entity-Ref-ID": uuid.uuid4().hex},
    }

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
            resp = await client.post(
                _RESEND_ENDPOINT,
                json=payload,
                headers={"Authorization": f"Bearer {api_key}"},
            )
        if resp.status_code >= 400:
            # Never log the body verbatim — it echoes the recipient address.
            logger.warning(
                "Resend rejected the early-access confirmation (status=%s) for %s. "
                "A sending domain must be verified in Resend before mail can "
                "reach addresses other than the account owner's.",
                resp.status_code,
                mask_email(to_email) or "-",
            )
            return
        logger.info(
            "Early-access confirmation sent to %s (seat %s).",
            mask_email(to_email) or "-",
            seat,
        )
    except Exception:
        logger.exception(
            "Failed to send the early-access confirmation to %s.",
            mask_email(to_email) or "-",
        )
