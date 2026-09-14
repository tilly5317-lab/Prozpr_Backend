"""FastAPI router — `early_access_router.py`.

The public, unauthenticated endpoints behind the invite-only launch:

- ``POST /early-access/signup``  — one application from the `/earlyaccess`
  page, appended to the team's Google Sheet, answering with the seat it took.
- ``GET  /early-access/seats``   — the live seat meter on that page.
- ``GET  /early-access/signup-status`` — whether the app's entry screen should
  offer account creation at all.

None of these take a session: a prospective early-access user has no account
yet, which is the whole point. The Sheet write is the only side effect, and it
is protected by a honeypot field, a per-IP window, and the Apps Script's
shared token — see ``services/early_access_service``.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, status

from app.core.config import get_settings
from app.core.pii import mask_email
from app.domains.early_access.schemas.early_access import (
    EarlyAccessSignupRequest,
    EarlyAccessSignupResponse,
    SeatsResponse,
    SignupStatusResponse,
)
from app.domains.early_access.services import early_access_service as svc
from app.domains.early_access.services.early_access_email_service import (
    send_early_access_confirmation,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/early-access", tags=["Early Access"])

_REGISTER_UNAVAILABLE = (
    "We couldn't save your request just now. Please try again in a moment."
)


def _client_key(request: Request) -> str:
    """Best-effort caller identity for the rate-limit window.

    Prozpr sits behind nginx on the same box, so the socket peer is always
    127.0.0.1 and ``X-Forwarded-For`` carries the real client. The left-most
    entry is the originating client; everything after it was added by proxies.
    A spoofed header only ever costs the spoofer their own quota, so trusting
    it here is fine — this is spam friction, not an access control.
    """
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        first = forwarded.split(",")[0].strip()
        if first:
            return first
    return request.client.host if request.client else "unknown"


def _effective_claimed(sheet_rows: int) -> int:
    """Seats the page should report as gone: the Sheet's rows plus any places
    already taken by people who never became rows (see the baseline setting)."""
    return get_settings().get_early_access_seats_baseline() + sheet_rows


def _seats(sheet_rows: int) -> dict[str, int]:
    total = get_settings().get_early_access_seats()
    claimed = _effective_claimed(sheet_rows)
    return {
        "seats_total": total,
        # Never overstates the cap: a full meter reads "100 of 100", and the
        # overflow shows up as `waitlisted` instead of a count past the total.
        "seats_claimed": min(total, claimed),
        # Never negative: once the waitlist runs past the cap the page should
        # read "0 seats left", not a negative number.
        "seats_left": max(0, total - claimed),
    }


@router.get("/signup-status", response_model=SignupStatusResponse)
async def signup_status() -> SignupStatusResponse:
    """Whether a brand-new number can create an account right now.

    Read per request (not cached) so flipping ``SIGNUPS_OPEN`` reopens the
    front door without a redeploy.
    """
    return SignupStatusResponse(signups_open=get_settings().signups_open())


@router.get("/seats", response_model=SeatsResponse)
async def seats() -> SeatsResponse:
    """The live seat meter. 503 when the Sheet cannot be read.

    Deliberately an error rather than a zero: the page falls back to a static
    count on failure, which is better than confidently rendering a wrong one.
    """
    try:
        claimed = await asyncio.to_thread(svc.fetch_claimed)
    except svc.EarlyAccessRegisterError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The seat count is unavailable right now.",
        )
    return SeatsResponse(**_seats(claimed))


@router.post(
    "/signup",
    response_model=EarlyAccessSignupResponse,
    status_code=status.HTTP_201_CREATED,
)
async def signup(
    payload: EarlyAccessSignupRequest,
    request: Request,
    background_tasks: BackgroundTasks,
) -> EarlyAccessSignupResponse:
    # A filled honeypot is a bot. Answer as if it worked: telling a scraper
    # which field gave it away is free information, and a real user can never
    # see this branch (the field is hidden and stays empty). Nothing is written,
    # and the seat numbers are left at the cap so the reply reveals nothing
    # about the real state of the list.
    if payload.referrer_note:
        # WARNING, not INFO, and it names the address: this branch throws an
        # application away silently, so if the trap ever starts catching real
        # people again it has to be visible in the logs rather than buried.
        logger.warning(
            "Early-access honeypot tripped — application from %s discarded.",
            mask_email(payload.email) or "-",
        )
        total = get_settings().get_early_access_seats()
        return EarlyAccessSignupResponse(
            seats_total=total, seats_claimed=total, seats_left=0, waitlisted=True
        )

    if svc.rate_limited(_client_key(request)):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                "You've already sent us a few requests. "
                "Give it a little while before trying again."
            ),
        )

    applied_at = datetime.now(timezone.utc)

    # The Sheet is the sole register, so this is awaited rather than deferred:
    # nobody is told they are on the list unless a row exists. The service call
    # is blocking `httpx`, hence the thread.
    try:
        result = await asyncio.to_thread(
            svc.append_application,
            applied_at=applied_at,
            name=payload.name,
            email=payload.email,
            whatsapp=payload.whatsapp,
            profession=payload.profession,
            source=payload.source or "earlyaccess_page",
        )
    except svc.EarlyAccessRegisterError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_REGISTER_UNAVAILABLE,
        )

    total = get_settings().get_early_access_seats()
    # Seat numbers are 1-based, so seat 100 is the last one inside a cap of 100.
    # Measured AFTER the baseline is applied, or a beta that is already part
    # full would keep handing out seats it does not have.
    waitlisted = (
        _effective_claimed(result.seat) > total
        if result.seat
        else _effective_claimed(result.claimed) > total
    )
    # The meter's next reader gets a count that already includes this row.
    svc.prime_seats_cache(result.claimed)

    # The row is safe; the ping is a convenience and runs after the response.
    # Skipped for a repeat submission — the team already heard about this
    # person, and a second ping reads like a second applicant.
    if not result.already_registered:
        # The applicant hears back straight away instead of filling a form and
        # then hearing nothing. Background, and best-effort inside: the row is
        # already safe, so a mail outage must not surface as a failed signup.
        background_tasks.add_task(
            send_early_access_confirmation,
            to_email=payload.email,
            full_name=payload.name,
            seat=result.seat,
            seats_total=total,
            waitlisted=waitlisted,
        )
        background_tasks.add_task(
            svc.notify_slack,
            applied_at=applied_at,
            name=payload.name,
            email=payload.email,
            whatsapp=payload.whatsapp,
            profession=payload.profession,
            seat=result.seat,
            waitlisted=waitlisted,
        )

    return EarlyAccessSignupResponse(
        **_seats(result.claimed),
        waitlisted=waitlisted,
        already_registered=result.already_registered,
    )
