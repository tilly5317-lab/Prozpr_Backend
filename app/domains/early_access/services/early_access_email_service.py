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
delete. A horizontal ticket carries the identity — holder, reference, issue date,
barcode — and everything conversational sits *below* it, outside the ticket,
where a letter belongs. The ticket is the object; the words are the covering
note.

It is built to read as a wealth advisory would: restraint over decoration.
Instrument Serif for the two moments that matter (the holder's name and the
ticket reference), small letterspaced capitals for everything structural, hairlines
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

import hashlib
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

# Bar height, repeated on every cell — see _barcode for why it is not on the table.
_BARCODE_H = 28


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
    that seat and no two tickets look identical.

    Every cell carries its height three ways — the `height` attribute, an
    inline `height`, and a `line-height` the &nbsp; can actually fill. The
    first version set the height on the TABLE and left the cells at
    `font-size:0`, so they collapsed to nothing and the barcode was invisible
    in real inboxes: a table's height is advisory in most mail clients, and a
    zero-size space cannot hold a row open. `bgcolor` sits alongside the inline
    background for the same belt-and-braces reason.
    """
    digits = [int(c) for c in f"{seed:06d}"]
    cells: list[str] = []
    for i in range(18):
        d = digits[i % len(digits)]
        bar = 2 + ((d + i) % 3)  # 2-4px bar
        gap = 2 + ((d + i) % 2)  # 2-3px gap, with its own rhythm
        cells.append(
            f'<td class="tk-bar" width="{bar}" height="{_BARCODE_H}" '
            f'bgcolor="{_INK}" style="width:{bar}px;height:{_BARCODE_H}px;'
            f"background-color:{_INK};font-size:1px;line-height:{_BARCODE_H}px"
            '">&nbsp;</td>'
            f'<td width="{gap}" height="{_BARCODE_H}" style="width:{gap}px;'
            f"height:{_BARCODE_H}px;font-size:1px;line-height:{_BARCODE_H}px"
            '">&nbsp;</td>'
        )
    return (
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
        'align="center" style="border-collapse:collapse;margin:0 auto"><tr>'
        + "".join(cells)
        + "</tr></table>"
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
  /* The layout STACKS BY DEFAULT and needs no media query to do it — see the
     fluid-hybrid comment on the ticket body. Gmail's mobile app strips this
     whole <style> block for many account types, so anything load-bearing put
     here would simply not happen on the phones most of these people use.
     What is left is a desktop enhancement: side by side, the perforation
     belongs on the left edge rather than the top. Lose it and the ticket is
     still correct, just with its tear line above the stub. */
  /* MOBILE IS THE DEFAULT, and it is the default INLINE: both panels are full
     width, stacked, with the tear line across the top of the stub. Gmail's
     mobile app strips this whole block for many account types, and stacked
     full width is exactly what those phones should get.

     Side by side is the DESKTOP enhancement. Capping the panels here rather
     than inline is what fixes the stacked look: a max-width of 236 left the
     stub at 236 on a 356-wide phone, so the perforation stopped dead at 45%
     and the seat block sat off-centre under a half-drawn line.

     630px, not 600: the panels total 594, and with the card border and the
     32px gutter they only really fit side by side from about 628. */
  @media only screen and (min-width:630px) {
    .tk-main { max-width:358px !important; }
    .tk-stub {
      max-width:236px !important;
      border-top:0 !important;
      border-left:1px dashed __RULE__ !important;
    }
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
    .tk-step  { color:#E0B84A !important; }
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
                           text-transform:uppercase;color:__GOLD__">Early access</td>
              </tr>
            </table>
          </td>
        </tr>
        <!-- One gold hairline, doing the work a coloured panel would do badly.
             Height stated three ways for the same reason as the barcode bars:
             a cell whose only content is a zero-sized space collapses, and the
             rule disappears. -->
        <tr><td height="2" bgcolor="__GOLD__"
                style="height:2px;background-color:__GOLD__;font-size:1px;
                       line-height:2px">&nbsp;</td></tr>
        <tr>
          <td style="padding:0">
            <!-- FLUID HYBRID. Two inline-block panels with max-widths that add
                 up to the ticket: wide enough and they sit side by side, too
                 narrow and the second wraps under the first ON ITS OWN. No
                 media query is involved, which matters because Gmail's mobile
                 app drops <style> for many accounts — a percentage-column
                 table would stay side by side there and crush to nothing.
                 font-size:0 on the wrapper kills the whitespace gap that
                 inline-block elements otherwise render between them; each
                 panel sets its own size back. The MSO conditionals give
                 Outlook a real table, since it ignores inline-block. -->
            <div style="font-size:0;text-align:left">
              <!--[if mso]><table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr><td width="358" valign="top"><![endif]-->
              <div class="tk-main" style="display:inline-block;width:100%;
                          max-width:100%;vertical-align:top;font-size:14px">
                <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">
                  <tr>
                    <td style="padding:26px 26px 28px 26px">
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
                              Prozpr &middot; {{EDITION}}
                            </p>
                            <p class="tk-muted" style="margin:6px 0 0 0;font-size:10px;font-weight:700;
                                      letter-spacing:1.6px;text-transform:uppercase;color:__MUTED__">
                              Issued {{ISSUED}}
                            </p>
                          </td>
                        </tr>
                      </table>
                    </td>
                  </tr>
                </table>
              </div><!--[if mso]></td><td width="236" valign="top"><![endif]--><div
                   class="tk-stub"
                   style="display:inline-block;width:100%;max-width:100%;
                          vertical-align:top;font-size:14px;
                          border-top:1px dashed __RULE__">
                <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">
                  <tr>
                    <td align="center" style="padding:24px 20px 28px 20px">
                      <p class="tk-muted" style="margin:0;font-size:10px;font-weight:700;
                                letter-spacing:2px;text-transform:uppercase;color:__MUTED__">
                        {{SEAT_LABEL}}
                      </p>
                      <p class="tk-seat" style="margin:12px 0 0 0;font-family:'Instrument Serif',
                                Georgia,'Times New Roman',serif;font-size:27px;line-height:1.1;
                                letter-spacing:1.5px;color:__INK__">{{SEAT_VALUE}}</p>
                      <p class="tk-muted" style="margin:6px 0 0 0;font-size:10px;font-weight:700;
                                letter-spacing:1.6px;text-transform:uppercase;color:__MUTED__">
                        {{SEAT_SUB}}
                      </p>
                      <div style="margin-top:18px">{{BARCODE}}</div>
                    </td>
                  </tr>
                </table>
              </div>
              <!--[if mso]></td></tr></table><![endif]-->
            </div>
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
                  {{STEPS}}
                  <p class="tk-muted" style="margin:22px 0 0 0;font-size:13px;line-height:1.7;
                            color:__MUTED__">
                    Early access is free throughout. For your security, please remember
                    that Prozpr asks for payment details or a one-time password only
                    inside the app &mdash; never over email or WhatsApp.
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
              You are receiving this because you requested early access at
              <a href="{{SITE_URL}}" style="color:#9ca3af;text-decoration:underline">{{SITE_HOST}}</a>.
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


# What the ticket calls the programme. Deliberately a NAME, not a version.
#
# The mail used to print an edition ("Beta 2.0") beside the wordmark and in
# every subject line. A version number tells the reader they have been handed
# a numbered pre-release — our engineering vocabulary leaking into a letter
# sent to prospective customers; "MVP" is worse still. What a recipient needs
# to know is that they are early, which is exactly what "early access" says.
# Keep version numbers out of anything a tester reads.
_PROGRAMME = "early access"
_PROGRAMME_TITLE = "Early access"

# Letters only, and no I or O (they read as 1 and 0). A reference that cannot
# be mistaken for a number is the whole point — see _reference.
_REF_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ"


def _reference(seat: int) -> str:
    """The short ticket reference printed on the stub.

    Derived from the seat, so one ticket always carries the same code and a
    resend never issues a new one, but *hashed into letters* so it cannot be
    read back as a position in the queue. That matters: the /earlyaccess page
    deliberately reports a different figure from the register, so a code that
    decoded to "you are number 37" would contradict the page — and would tell
    two recipients who compare tickets exactly who arrived first.
    """
    digest = hashlib.sha256(f"prozpr-early-access:{seat}".encode()).digest()
    code = "".join(_REF_ALPHABET[b % len(_REF_ALPHABET)] for b in digest[:4])
    return f"PZ-{code}"


def _step(n: int, title: str, body: str) -> str:
    """One numbered row of "What happens next". A table row, not a list: `<ol>`
    markers render at a different size and indent in every client."""
    return (
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
        'width="100%" style="margin-top:16px"><tr>'
        '<td width="34" valign="top" style="width:34px;padding-top:1px">'
        '<p class="tk-step" style="margin:0;font-family:\'Instrument Serif\',Georgia,'
        f"'Times New Roman',serif;font-size:20px;line-height:1;color:#A8801F\">"
        f"{n:02d}</p></td>"
        '<td valign="top">'
        '<p class="tk-head" style="margin:0;font-size:14px;font-weight:600;'
        f'line-height:1.4;color:{_INK}">{html.escape(title)}</p>'
        '<p class="tk-body" style="margin:3px 0 0 0;font-size:14px;line-height:1.65;'
        f'color:{_BODY}">{html.escape(body)}</p>'
        "</td></tr></table>"
    )


def _render(
    *,
    full_name: str,
    seat: int,
    seats_total: int,
    waitlisted: bool,
    issued_on: date | None = None,
) -> tuple[str, str, str]:
    """Build (subject, text, html) for one applicant.

    `seat` and `seats_total` only decide WHICH ticket is sent (the script
    waitlists anyone past the cap), seed the barcode, and derive the reference
    code. Neither number is printed: the /earlyaccess page shows a smaller
    seats-left count than the register holds, so a "Seat 037 of 100" in the
    mail would contradict what the person saw when they applied.

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
    site_url = get_settings().get_public_site_url()
    host = site_url.split("://", 1)[-1].rstrip("/")
    issued = (issued_on or datetime.now(IST).date()).strftime("%d %b %Y").upper()

    if waitlisted:
        subject = "You're on the standby list for Prozpr early access"
        preheader = (
            "Every seat in this round is taken. You are next in line, and you "
            "hear from us first."
        )
        admit, admit_color = "Standby", _MUTED
        role = "Next in line"
        seat_sub = "Standby"
        heading = f"You're next in line, {safe_first}."
        heading_text = f"You're next in line, {first}."
        intro = (
            "Thank you for putting your hand up. Every seat in this round is "
            "already taken, so this is a standby ticket: your place in line is "
            "held, and places do open."
        )
        steps = [
            (
                "Your place in line is held",
                "Places free up most weeks as the round settles. When one opens, "
                "we write to you before anyone else.",
            ),
            (
                "Your invite follows",
                "As soon as a place is yours, your invite and the tester group "
                "link come to this address.",
            ),
            (
                "Everything is saved",
                "Your request is on file, so you can leave this with us and "
                "carry on.",
            ),
        ]
    else:
        subject = "You're in: Prozpr early access"
        preheader = (
            "Your place in Prozpr early access is confirmed. "
            "Here is what happens next."
        )
        admit, admit_color = "Admit one", _RED
        role = "Founding tester"
        # Reads under the reference on the stub, and again in the plain-text
        # part. "Early access" there would repeat the line above it verbatim.
        seat_sub = "Confirmed"
        heading = f"You're in, {safe_first}."
        heading_text = f"You're in, {first}."
        intro = (
            "Thank you for putting your hand up. Your place in Prozpr early "
            "access is confirmed, which means you see the new Prozpr well before "
            "it opens to everyone, and your reading of it shapes what launches. "
            "This ticket is yours to keep."
        )
        steps = [
            (
                "Your invite arrives",
                "Before early access opens, we send your invite and the tester "
                "group link to this address.",
            ),
            (
                "Use it with your own portfolio",
                "Work with it the way you would for real. That is where the most "
                "useful observations come from.",
            ),
            (
                "Tell us what you think",
                "Your feedback decides what we refine and build next, while there "
                "is still time to act on it.",
            ),
        ]

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
        .replace("{{ISSUED}}", html.escape(issued))
        .replace("{{EDITION}}", html.escape(_PROGRAMME_TITLE))
        .replace("{{SEAT_LABEL}}", "Reference")
        .replace("{{SEAT_VALUE}}", html.escape(_reference(seat)))
        .replace("{{SEAT_SUB}}", html.escape(seat_sub))
        .replace("{{BARCODE}}", _barcode(seat))
        .replace("{{HEADING}}", heading)
        .replace("{{INTRO}}", html.escape(intro))
        .replace(
            "{{STEPS}}", "".join(_step(i, t, b) for i, (t, b) in enumerate(steps, 1))
        )
        .replace("{{SITE_URL}}", html.escape(site_url))
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
            f"Prozpr {_PROGRAMME} - {seat_sub}, ref {_reference(seat)}, "
            f"issued {issued}",
            "",
            "WHAT HAPPENS NEXT",
            *(f"{i}. {t}: {b}" for i, (t, b) in enumerate(steps, 1)),
            "",
            "Early access is free throughout. For your security, please remember "
            "that Prozpr asks for payment details or a one-time password only "
            "inside the app - never over email or WhatsApp.",
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
