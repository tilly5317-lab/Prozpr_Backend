"""The early-access mail: a ticket, rendered for one applicant.

**Nothing here is sent automatically.** `/early-access/signup` does not import
this module — a form being tested, spammed or replayed must never mail anyone.
Mail goes out in deliberate batches the team triggers itself, through
``scripts/send_early_access_mails.py``, which defaults to a dry run.

Sent through Resend (``RESEND_API_KEY``), the same channel and verified sender
as the forgot-PIN code.

## The design

The message is a **ticket**: the applicant claimed their way into something,
and the mail should feel like the stub you keep rather than a receipt you
delete. A horizontal ticket carries the identity — holder, seat, issue date,
barcode — and everything conversational sits *below* it, outside the ticket,
where a letter belongs. The ticket is the object; the words are the covering
note.

It is built to read as a wealth advisory would: restraint over decoration.
Instrument Serif for the two moments that matter (the holder's name and the
seat number), small letterspaced capitals for everything structural, hairlines
rather than filled blocks, and a single gold rule doing the work a coloured
panel would do badly. The palette is the /earlyaccess page's own, so the mail
and the page are recognisably one thing.

## Email constraints this obeys

- Tables and inline styles only; no flexbox or grid.
- The wordmark is TEXT, not an image — most clients block remote images by
  default, so a logo file leaves a blank box for a large share of recipients.
  Instrument Serif with Georgia behind it; both carry the rupee glyph.
- The barcode is table cells with background colours, so it survives an image
  blocker too.
- A dark-mode block, because a cream ticket inverted by a client's own filter
  looks broken.
- Rounded corners and dashed borders degrade to square and solid in Outlook
  desktop, which is acceptable; nothing load-bearing depends on them.
"""

from __future__ import annotations

import html
import logging
import uuid
from datetime import date, datetime, timedelta, timezone

import httpx

from app.core.config import get_settings
from app.core.pii import mask_email

logger = logging.getLogger(__name__)

_RESEND_ENDPOINT = "https://api.resend.com/emails"
_TIMEOUT_S = 10.0

try:
    from zoneinfo import ZoneInfo

    IST = ZoneInfo("Asia/Kolkata")
except Exception:  # Windows without `tzdata` — the fixed offset is exact for IST.
    IST = timezone(timedelta(hours=5, minutes=30), "IST")

# The /earlyaccess page's palette, so the mail and the page read as one thing.
_INK = "#111113"
_CREAM = "#F7F3EC"
_GOLD = "#E0B84A"
_RED = "#C8321F"
_MUTED = "#8A8275"
_BODY = "#57534A"
_RULE = "#E8E2D2"


def _first_name(full_name: str) -> str:
    """The name to greet by. Falls back to a neutral greeting rather than an
    empty one — "Hi ," reads worse than no name at all."""
    first = (full_name or "").strip().split(" ")[0].strip()
    return first if first else "there"


def _holder_name(full_name: str) -> str:
    """The name printed on the ticket. The full name where we have one, because
    a ticket carries a holder rather than a greeting."""
    name = " ".join((full_name or "").split())
    return name if name else "Founding tester"


def _barcode(seed: int) -> str:
    """A barcode strip built from table cells, so an image blocker cannot strip
    it. Bar widths derive from the seat number, so a ticket's code is stable for
    that seat and no two tickets look identical."""
    digits = [int(c) for c in f"{seed:06d}"]
    bars: list[str] = []
    for i in range(30):
        d = digits[i % len(digits)]
        width = 1 + ((d + i) % 3)  # 1-3px bars
        gap = 1 + ((d + i) % 2)  # with their own rhythm between
        bars.append(
            f'<td class="tk-bar" width="{width}" style="width:{width}px;'
            f'background-color:{_INK};font-size:0;line-height:0">&nbsp;</td>'
            f'<td width="{gap}" style="width:{gap}px;font-size:0;line-height:0">&nbsp;</td>'
        )
    return (
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
        'style="height:30px"><tr>' + "".join(bars) + "</tr></table>"
    )


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
  @media only screen and (max-width:520px) {
    .tk-outer { padding:18px 10px !important; }
    .tk-col   { display:block !important; width:100% !important; }
    .tk-stub  { border-left:0 !important; border-top:1px dashed __RULE__ !important; }
    .tk-name  { font-size:25px !important; }
    .tk-seat  { font-size:40px !important; }
    .tk-pad   { padding:22px !important; }
  }
  @media (prefers-color-scheme:dark) {
    .tk-page  { background-color:#0f1115 !important; }
    .tk-card  { background-color:#171a21 !important; border-color:#262b34 !important; }
    .tk-name  { color:#f0efe9 !important; }
    .tk-seat  { color:#f0efe9 !important; }
    .tk-body  { color:#c3c8d1 !important; }
    .tk-muted { color:#9aa1ac !important; }
    .tk-head  { color:#f0efe9 !important; }
    .tk-rule  { border-color:#2f3641 !important; }
    .tk-stub  { border-color:#2f3641 !important; }
    .tk-foot  { color:#767d88 !important; }
    .tk-bar   { background-color:#c3c8d1 !important; }
  }
</style>
</head>
<body class="tk-page" style="margin:0;padding:0;width:100%;background-color:__CREAM__;">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;mso-hide:all" aria-hidden="true">
  {{PREHEADER}}
</div>
<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"
       class="tk-page" style="background-color:__CREAM__">
  <tr>
    <td align="center" class="tk-outer" style="padding:40px 16px">

      <!-- ─────────────────────── THE TICKET ─────────────────────── -->
      <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"
             class="tk-card"
             style="max-width:600px;background-color:#ffffff;border:1px solid __RULE__;
                    border-radius:18px;overflow:hidden;
                    font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,
                    Helvetica,Arial,sans-serif">
        <tr>
          <td style="background-color:__INK__;padding:18px 26px">
            <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">
              <tr>
                <td align="left" style="font-family:'Instrument Serif',Georgia,
                           'Times New Roman',serif;font-size:24px;line-height:1;
                           color:__CREAM__">prozp&#8377;<span style="color:__GOLD__">.</span></td>
                <td align="right" style="font-size:10px;font-weight:700;letter-spacing:1.8px;
                           text-transform:uppercase;color:__GOLD__">Private beta</td>
              </tr>
            </table>
          </td>
        </tr>
        <!-- One gold hairline, doing the work a coloured panel would do badly. -->
        <tr><td style="height:2px;background-color:__GOLD__;font-size:0;line-height:0">&nbsp;</td></tr>
        <tr>
          <td>
            <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">
              <tr>
                <td class="tk-col tk-pad" width="62%" valign="top"
                    style="width:62%;padding:26px 26px 28px 26px">
                  <p style="margin:0;font-size:10px;font-weight:700;letter-spacing:2px;
                            text-transform:uppercase;color:__ADMIT_COLOR__">{{ADMIT}}</p>
                  <p class="tk-name" style="margin:10px 0 0 0;font-family:'Instrument Serif',
                            Georgia,'Times New Roman',serif;font-size:29px;line-height:1.15;
                            color:__INK__">{{HOLDER}}</p>
                  <p class="tk-body" style="margin:5px 0 0 0;font-size:13px;color:__BODY__">
                    {{ROLE}}
                  </p>
                  <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"
                         class="tk-rule" style="border-top:1px solid __RULE__;margin-top:20px">
                    <tr>
                      <td style="padding-top:16px">
                        <p class="tk-muted" style="margin:0;font-size:10px;font-weight:700;
                                  letter-spacing:1.6px;text-transform:uppercase;color:__MUTED__">
                          Prozpr MVP 2.0 &middot; {{TOTAL}} seats
                        </p>
                        <p class="tk-muted" style="margin:6px 0 0 0;font-size:10px;font-weight:700;
                                  letter-spacing:1.6px;text-transform:uppercase;color:__MUTED__">
                          Issued {{ISSUED}}
                        </p>
                      </td>
                    </tr>
                  </table>
                </td>
                <!-- Perforation: a dashed rule reads as a tear line without the
                     absolutely-positioned notches email cannot place reliably. -->
                <td class="tk-col tk-stub tk-pad" width="38%" valign="top" align="center"
                    style="width:38%;padding:26px 22px 28px 22px;border-left:1px dashed __RULE__">
                  <p class="tk-muted" style="margin:0;font-size:10px;font-weight:700;
                            letter-spacing:2px;text-transform:uppercase;color:__MUTED__">
                    {{SEAT_LABEL}}
                  </p>
                  <p class="tk-seat" style="margin:8px 0 0 0;font-family:'Instrument Serif',
                            Georgia,'Times New Roman',serif;font-size:46px;line-height:1;
                            color:__INK__">{{SEAT_VALUE}}</p>
                  <p class="tk-muted" style="margin:6px 0 0 0;font-size:10px;font-weight:700;
                            letter-spacing:1.6px;text-transform:uppercase;color:__MUTED__">
                    {{SEAT_SUB}}
                  </p>
                  <div style="margin-top:18px">{{BARCODE}}</div>
                </td>
              </tr>
            </table>
          </td>
        </tr>
      </table>

      <!-- ──────────── THE COVERING NOTE (outside the ticket) ──────────── -->
      <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"
             style="max-width:600px;
                    font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,
                    Helvetica,Arial,sans-serif">
        <tr>
          <td style="padding:30px 8px 0 8px">
            <p class="tk-head" style="margin:0;font-family:'Instrument Serif',Georgia,
                      'Times New Roman',serif;font-size:21px;line-height:1.3;color:__INK__">
              {{HEADING}}
            </p>
            <p class="tk-body" style="margin:12px 0 0 0;font-size:15px;line-height:1.65;
                      color:__BODY__">{{INTRO}}</p>
          </td>
        </tr>
        <tr>
          <td style="padding:26px 8px 0 8px">
            <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"
                   class="tk-rule" style="border-top:1px solid __RULE__">
              <tr>
                <td style="padding-top:22px">
                  <p class="tk-muted" style="margin:0;font-size:10px;font-weight:700;
                            letter-spacing:2px;text-transform:uppercase;color:__MUTED__">
                    What happens next
                  </p>
                  <p class="tk-body" style="margin:10px 0 0 0;font-size:14px;line-height:1.7;
                            color:__BODY__">{{NEXT}}</p>
                  <p class="tk-muted" style="margin:16px 0 0 0;font-size:13px;line-height:1.7;
                            color:__MUTED__">
                    The beta is free. Prozpr will never ask you for payment details
                    or a one-time password.
                  </p>
                </td>
              </tr>
            </table>
          </td>
        </tr>
        <tr>
          <td style="padding:26px 8px 0 8px">
            <p class="tk-foot" style="margin:0;font-size:11px;line-height:1.75;color:#9ca3af">
              Prozpr Private Limited &middot; Educational insights, not investment advice.
              AMFI and SEBI registration in process.<br>
              You are receiving this because you requested early access at {{SITE_HOST}}.
              Replies to this address are not monitored.
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
    *,
    full_name: str,
    seat: int,
    seats_total: int,
    waitlisted: bool,
    issued_on: date | None = None,
) -> tuple[str, str, str]:
    """Build (subject, text, html) for one applicant.

    Every interpolated value is either an int we produced or an HTML-escaped
    string. The name comes from a public form, so dropping it in raw would let
    a submitted name inject markup into a mail sent under our own domain.
    """
    first = _first_name(full_name)
    holder = _holder_name(full_name)
    # Two forms on purpose: the HTML part needs the escaped name, the plain
    # text part needs the real one. Reusing the escaped string for both is how
    # "O'Brien" reaches an inbox as "O&#x27;Brien".
    safe_first = html.escape(first)
    safe_holder = html.escape(holder)
    host = get_settings().get_public_site_url().split("://", 1)[-1].rstrip("/")
    issued = (issued_on or datetime.now(IST).date()).strftime("%d %b %Y").upper()

    if waitlisted:
        position = seat - seats_total if seat > seats_total else 1
        subject = "Your Prozpr beta standby ticket"
        preheader = (
            f"All {seats_total} seats are taken. You hold standby position {position}."
        )
        admit, admit_color = "Standby", _MUTED
        role = "Next in line for a seat"
        seat_label, seat_value, seat_sub = "Position", f"{position:02d}", "On standby"
        heading = f"You are next in line, {safe_first}."
        heading_text = f"You are next in line, {first}."
        intro = (
            f"Thank you for asking to test Prozpr MVP 2.0. All {seats_total} seats "
            "were taken before your request reached us, so this is a standby "
            "ticket rather than a seat."
        )
        next_line = (
            "Seats are confirmed in sign-up order, and testers do drop out. If a "
            "place opens we will write to you before anyone else. There is "
            "nothing you need to do in the meantime."
        )
        text_seat = f"Standby position: {position:02d}"
    else:
        subject = "Your Prozpr beta ticket is confirmed"
        preheader = (
            f"Seat {seat:03d} of {seats_total} is held in your name. "
            "Here is what happens next."
        )
        admit, admit_color = "Admit one", _RED
        role = "Founding tester"
        seat_label = "Seat"
        seat_value = f"{seat:03d}"
        seat_sub = f"of {seats_total}"
        heading = f"Your seat is held, {safe_first}."
        heading_text = f"Your seat is held, {first}."
        intro = (
            "Thank you for putting your hand up. We are glad to have you testing "
            "Prozpr MVP 2.0 before anyone else, and this ticket is yours to keep."
        )
        next_line = (
            "We will write with your invite and the tester group link before the "
            "beta opens. Seats are confirmed in sign-up order, so nothing can "
            "take yours. There is nothing you need to do until then."
        )
        text_seat = f"Seat {seat:03d} of {seats_total}"

    html_body = (
        _HTML_TEMPLATE.replace("__INK__", _INK)
        .replace("__CREAM__", _CREAM)
        .replace("__GOLD__", _GOLD)
        .replace("__MUTED__", _MUTED)
        .replace("__BODY__", _BODY)
        .replace("__RULE__", _RULE)
        .replace("__ADMIT_COLOR__", admit_color)
        .replace("{{TITLE}}", html.escape(subject))
        .replace("{{PREHEADER}}", html.escape(preheader))
        .replace("{{ADMIT}}", html.escape(admit))
        .replace("{{HOLDER}}", safe_holder)
        .replace("{{ROLE}}", html.escape(role))
        .replace("{{TOTAL}}", str(seats_total))
        .replace("{{ISSUED}}", html.escape(issued))
        .replace("{{SEAT_LABEL}}", html.escape(seat_label))
        .replace("{{SEAT_VALUE}}", html.escape(seat_value))
        .replace("{{SEAT_SUB}}", html.escape(seat_sub))
        .replace("{{BARCODE}}", _barcode(seat))
        .replace("{{HEADING}}", heading)
        .replace("{{INTRO}}", html.escape(intro))
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
            f"{holder} - {role}",
            text_seat,
            f"Prozpr MVP 2.0, issued {issued}",
            "",
            "WHAT HAPPENS NEXT",
            next_line,
            "",
            "The beta is free. Prozpr will never ask you for payment details or "
            "a one-time password.",
            "",
            f"You are receiving this because you requested early access at {host}.",
            "Replies to this address are not monitored.",
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
    issued_on: date | None = None,
) -> bool:
    """Send one ticket. Returns True if Resend accepted it.

    Logs every failure and returns False rather than raising, so a batch run
    reports what it managed and carries on to the next recipient instead of
    stopping partway through a list.
    """
    settings = get_settings()
    api_key = settings.get_resend_api_key()
    if not api_key:
        logger.warning(
            "RESEND_API_KEY not set - no mail sent to %s.", mask_email(to_email) or "-"
        )
        return False

    subject, text_body, html_body = _render(
        full_name=full_name,
        seat=seat,
        seats_total=seats_total,
        waitlisted=waitlisted,
        issued_on=issued_on,
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
                "Resend rejected the ticket mail (status=%s) for %s. A sending "
                "domain must be verified in Resend before mail can reach "
                "addresses other than the account owner's.",
                resp.status_code,
                mask_email(to_email) or "-",
            )
            return False
        logger.info(
            "Ticket mail sent to %s (seat %s).", mask_email(to_email) or "-", seat
        )
        return True
    except Exception:
        logger.exception(
            "Failed to send the ticket mail to %s.", mask_email(to_email) or "-"
        )
        return False
