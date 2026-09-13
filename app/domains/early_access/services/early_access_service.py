"""Early-access register: a Google Sheet row per application, + a Slack ping.

There is deliberately **no database table** for early-access applications. The
shared Google Sheet IS the register, the same arrangement the issue register
uses (``support/services/issue_report_service``) — and, because it is the only
copy, it is also where the seat count is read back from. Keeping a counter here
as well would be a second source of truth that drifts the first time someone
edits the Sheet by hand, which is exactly what the team will do while working
through the list.

- ``EARLY_ACCESS_SHEET_WEBHOOK_URL`` (.env) — a Google Apps Script web app
  bound to the team's Sheet, deployed with **both** handlers:

  * ``doPost``  — appends one row and answers
    ``{"ok": true, "seat": <n>, "claimed": <n>, "already_registered": <bool>}``.
    It de-duplicates on the email column: a repeat submission returns the
    seat that email already holds instead of appending a second row.
  * ``doGet``   — answers ``{"ok": true, "claimed": <n>}`` for the seat meter.

  Row shape: ``Date | Name | Email | WhatsApp | Profession | Source | Seat |
  Notes`` (``Notes`` stays empty — it is the team's column to fill while
  working the list: *Invited*, *Onboarded*, *Passed*.) The script itself is in
  ``CLAUDE.md`` in this package.
  ``EARLY_ACCESS_SHEET_TOKEN`` is a shared secret the script checks so
  strangers cannot post junk rows if the URL leaks.

  Because the Sheet is the **sole** register, ``append_application`` raises on
  every failure mode (webhook unset, network error, script rejection) so the
  router answers 503 and the applicant is asked to try again — a lead is never
  lost to a silent success.

- ``SLACK_EARLY_ACCESS_WEBHOOK_URL`` (optional) — a ping so the team hears
  about an application without opening the Sheet. Best-effort: logged, never
  raised, skipped when unset.

**On PII.** Signup pings and the issue register mask name/email/phone before
they leave the system. This register does **not**: its entire purpose is for
the team to *contact* these hundred people, and a masked number cannot be
dialled. The trade-off is deliberate and has a cost worth knowing — rows here
are outside the database, so an erasure request cannot reach them
automatically and someone has to clear the row by hand. Keep the Sheet
restricted to the team, and delete a row once that person is onboarded (their
record then lives in the database, where erasure works) or has declined.

Every function here is synchronous on purpose: the router runs the Sheet calls
via ``asyncio.to_thread`` and the Slack ping as a background task, so neither
blocks the event loop.
"""

from __future__ import annotations

import logging
import os
import ssl
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache

import httpx

from app.core.config import get_settings
from app.core.pii import mask_email, mask_mobile

logger = logging.getLogger(__name__)

try:
    from zoneinfo import ZoneInfo

    IST = ZoneInfo("Asia/Kolkata")
except Exception:  # Windows without `tzdata` — the fixed offset is exact for IST.
    IST = timezone(timedelta(hours=5, minutes=30), "IST")


@lru_cache(maxsize=1)
def _ssl_context() -> ssl.SSLContext:
    """A fully-verifying TLS context seeded from the OS trust store.

    Mirrors ``signup_notification_service._ssl_context``: ``httpx`` defaults
    ``verify=`` to certifi's bundled ``cacert.pem``, which raises
    ``FileNotFoundError`` when that data file is missing from the virtualenv —
    the exact failure that once silently dropped the signup pings. Seeding from
    the operating system keeps outbound HTTPS working regardless; certifi's
    bundle is loaded on top when it is present.
    """
    ctx = ssl.create_default_context()
    try:
        import certifi

        ca = certifi.where()
        if os.path.exists(ca):
            ctx.load_verify_locations(cafile=ca)
    except Exception:
        pass
    return ctx


# Column order one application maps to — keep in sync with the Apps Script's
# HEADERS (the script is reproduced in CLAUDE.md in this package).
SHEET_HEADERS = [
    "Date",
    "Name",
    "Email",
    "WhatsApp",
    "Profession",
    "Source",
    "Seat",
    "Notes",
]

# The applicant is waiting on the append (the Sheet is the sole register, so it
# cannot be deferred to a background task), so this is tighter than the
# fire-and-forget signup ping's timeout.
_TIMEOUT_SECONDS = 10

# A cold Apps Script deployment can take a few seconds to wake and answer 5xx
# meanwhile; that is worth one retry. A 4xx is a configuration error and
# retrying it only makes the applicant wait longer.
_RETRY_DELAYS = (1.5,)


class EarlyAccessRegisterError(RuntimeError):
    """A Sheet call failed. The router turns this into a 503 so the applicant
    retries, rather than being told they are on the list when no row exists."""


@dataclass(frozen=True)
class SeatResult:
    """What the Sheet said after an append (or a lookup of an existing email)."""

    seat: int
    claimed: int
    already_registered: bool


# ── Spam friction ───────────────────────────────────────────────────────────
# `/early-access/signup` is public and unauthenticated, and it writes to a
# third-party document, so an open loop would let anyone fill the Sheet with
# junk and bury the real applicants. Two cheap defences, neither of which a
# real applicant will ever notice:
#   1. the honeypot field on the schema (a bot fills every input);
#   2. this per-IP window.
# In-memory on purpose. Prozpr runs on a single box, and the failure mode of a
# per-process counter — a determined attacker spreading load across workers —
# is a problem for a real rate limiter and a WAF, not for the friction this is.
_RATE_LIMIT_MAX = 5
_RATE_LIMIT_WINDOW_SECONDS = 15 * 60
_rate_lock = threading.Lock()
_rate_hits: dict[str, list[float]] = {}


def rate_limited(client_key: str) -> bool:
    """True when `client_key` has already applied ``_RATE_LIMIT_MAX`` times in
    the last window. Also prunes expired entries, so the dict cannot grow
    without bound across a long-running process."""
    now = time.monotonic()
    cutoff = now - _RATE_LIMIT_WINDOW_SECONDS
    with _rate_lock:
        for key in [k for k, v in _rate_hits.items() if not v or v[-1] < cutoff]:
            del _rate_hits[key]
        hits = [t for t in _rate_hits.get(client_key, []) if t >= cutoff]
        if len(hits) >= _RATE_LIMIT_MAX:
            _rate_hits[client_key] = hits
            return True
        hits.append(now)
        _rate_hits[client_key] = hits
        return False


# ── Talking to the Sheet ────────────────────────────────────────────────────
def _webhook_url() -> str:
    url = get_settings().get_early_access_sheet_webhook_url()
    if not url:
        raise EarlyAccessRegisterError(
            "EARLY_ACCESS_SHEET_WEBHOOK_URL is not configured — "
            "the early-access register is unreachable."
        )
    return url


def _call_sheet(
    method: str, *, json: dict | None = None, params: dict | None = None
) -> dict:
    """One request to the Apps Script web app, with a single retry on 5xx.

    Returns the decoded JSON body. Raises ``EarlyAccessRegisterError`` on any
    failure — transport, HTTP status, undecodable body, or an ``ok: false``
    answer from the script itself (a rejected token looks like that).
    """
    url = _webhook_url()
    last_error: Exception | None = None
    for attempt in range(len(_RETRY_DELAYS) + 1):
        try:
            # Apps Script answers with a 302 to script.googleusercontent.com —
            # follow it, or every call looks like a redirect rather than a row.
            resp = httpx.request(
                method,
                url,
                json=json,
                params=params,
                timeout=_TIMEOUT_SECONDS,
                follow_redirects=True,
                verify=_ssl_context(),
            )
            resp.raise_for_status()
            body = resp.json()
            if not isinstance(body, dict) or not body.get("ok"):
                raise EarlyAccessRegisterError(
                    f"Early-access Apps Script rejected the call: {body!r}"
                )
            return body
        except httpx.HTTPStatusError as exc:
            last_error = exc
            # 4xx is us: a wrong URL or a rejected token. Retrying cannot fix it.
            if exc.response.status_code < 500:
                break
        except Exception as exc:
            last_error = exc
        if attempt < len(_RETRY_DELAYS):
            time.sleep(_RETRY_DELAYS[attempt])

    logger.error("Early-access Sheet call failed (%s).", method, exc_info=last_error)
    raise EarlyAccessRegisterError("The early-access register is unreachable.") from (
        last_error
    )


def append_application(
    *,
    applied_at: datetime,
    name: str,
    email: str,
    whatsapp: str | None,
    profession: str,
    source: str,
) -> SeatResult:
    """Append one application to the Sheet and report the seat it took.

    The script de-duplicates on email, so submitting the same address twice
    returns the seat already held with ``already_registered=True`` instead of
    adding a second row. RAISES on any failure — see the module docstring.
    """
    body = _call_sheet(
        "POST",
        json={
            "token": get_settings().get_early_access_sheet_token() or "",
            "date": applied_at.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S"),
            "name": name,
            "email": email,
            "whatsapp": whatsapp or "",
            "profession": profession,
            "source": source or "",
        },
    )
    already = bool(body.get("already_registered"))
    logger.info(
        "Early-access application recorded (seat=%s, already_registered=%s).",
        body.get("seat"),
        already,
    )
    return SeatResult(
        seat=int(body.get("seat") or 0),
        claimed=int(body.get("claimed") or 0),
        already_registered=already,
    )


# The meter is on a public page, so an uncached read would put one Apps Script
# round-trip on every page load — and Apps Script has a daily execution quota.
# A few seconds of staleness on "N seats left" costs nothing.
_SEATS_CACHE_SECONDS = 30
_seats_lock = threading.Lock()
_seats_cache: tuple[float, int] | None = None


def fetch_claimed(*, use_cache: bool = True) -> int:
    """How many seats the Sheet says are taken. RAISES if it cannot be read.

    The router lets that failure surface as a 503: the page falls back to a
    static count, which is better than confidently rendering a wrong one.
    """
    global _seats_cache
    if use_cache:
        with _seats_lock:
            if (
                _seats_cache
                and time.monotonic() - _seats_cache[0] < _SEATS_CACHE_SECONDS
            ):
                return _seats_cache[1]
    body = _call_sheet(
        "GET", params={"token": get_settings().get_early_access_sheet_token() or ""}
    )
    claimed = int(body.get("claimed") or 0)
    with _seats_lock:
        _seats_cache = (time.monotonic(), claimed)
    return claimed


def prime_seats_cache(claimed: int) -> None:
    """Seed the meter's cache from an append we just made, so the count the
    next visitor sees includes this application without another round-trip."""
    global _seats_cache
    with _seats_lock:
        _seats_cache = (time.monotonic(), claimed)


# ── Team ping ───────────────────────────────────────────────────────────────
def notify_slack(
    *,
    applied_at: datetime,
    name: str,
    email: str,
    whatsapp: str | None,
    profession: str,
    seat: int,
    waitlisted: bool,
) -> None:
    """Best-effort team ping. Logged, never raised, skipped when unset.

    Contact details are MASKED here even though the Sheet holds them in full:
    a channel is read by more people and retained far longer than a row the
    team prunes, and the ping only has to say *someone applied* — the Sheet is
    where you go to actually reach them.
    """
    url = get_settings().get_slack_early_access_webhook_url()
    if not url:
        logger.debug(
            "SLACK_EARLY_ACCESS_WEBHOOK_URL not set — early-access Slack ping skipped."
        )
        return
    when = applied_at.astimezone(IST).strftime("%d %b %Y, %I:%M %p IST")
    headline = (
        ":hourglass_flowing_sand: *New early-access request* (waitlist)"
        if waitlisted
        else ":sparkles: *New early-access request*"
    )
    lines = [
        headline,
        f"*Name:* {name or '-'}",
        f"*Email:* {mask_email(email) or '-'}",
        f"*WhatsApp:* {mask_mobile(whatsapp) or '-'}",
        f"*Profession:* {profession or '-'}",
        f"*Seat:* {seat or '-'}",
        f"*When:* {when}",
        "Full details are in the Early Access sheet.",
    ]
    try:
        resp = httpx.post(
            url,
            json={"text": "\n".join(lines)},
            timeout=_TIMEOUT_SECONDS,
            verify=_ssl_context(),
        )
        resp.raise_for_status()
        logger.info("Early-access application posted to Slack.")
    except Exception:
        logger.exception("Failed to post early-access application to Slack.")
