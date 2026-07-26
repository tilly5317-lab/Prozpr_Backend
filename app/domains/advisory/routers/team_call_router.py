"""FastAPI router — `team_call_router.py`.

Books real Zoom meetings for the portfolio page's "Talk to the Prozpr team"
card. No DB table: the meeting lives in Zoom; ownership for cancellation is
verified via an agenda marker (see ``zoom_service``). 503 when the ZOOM_* env
vars are unset so the frontend can fall back to its static link.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import CurrentUser, get_effective_user
from app.domains.advisory.schemas.team_call import (
    TeamCallCreateRequest,
    TeamCallResponse,
)
from app.domains.advisory.services import zoom_service
from app.domains.notifications.services.notification_service import (
    create_notification,
)

IST = timezone(timedelta(hours=5, minutes=30))

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/team-call", tags=["Advisory"])


def _owner_email(user: CurrentUser) -> str:
    # Email can be absent early in onboarding — fall back to the stable user id
    # so the ownership marker is still unique per user.
    return (user.email or "").strip().lower() or f"user-{user.id}"


@router.post("", response_model=TeamCallResponse, status_code=status.HTTP_201_CREATED)
async def book_team_call(
    payload: TeamCallCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    start = payload.start_time
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if start <= datetime.now(timezone.utc):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Pick a time in the future.",
        )

    name_parts = [current_user.first_name or "", current_user.last_name or ""]
    display_name = " ".join(p for p in name_parts if p).strip() or (
        current_user.email or str(current_user.id)
    )
    agenda = f"Booked by {display_name} ({current_user.email or 'no email'}).\n\n{payload.agenda}".strip()

    try:
        meeting = await zoom_service.create_team_call_meeting(
            start_utc=start,
            agenda=agenda,
            owner_email=_owner_email(current_user),
        )
    except zoom_service.ZoomNotConfigured as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Zoom booking is not configured yet. Please try again later.",
        ) from exc
    except zoom_service.ZoomApiError as exc:
        logger.error("Zoom create-meeting failed: %s", exc.detail)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not create the Zoom meeting right now. Please try again.",
        ) from exc

    join_url = str(meeting["join_url"])

    # Confirmation notification with the join link. Best-effort: the meeting
    # already exists in Zoom, so a notification hiccup must not fail the booking.
    try:
        ist = start.astimezone(IST)
        when = f"{ist.strftime('%a %d %b')}, {ist.strftime('%I:%M %p').lstrip('0')} IST"
        await create_notification(
            db,
            user_id=current_user.id,
            title="Your Prozpr team call is booked",
            message=(
                f"Your 15-minute Zoom call with the Prozpr team is confirmed for "
                f"{when}. Tap to open the meeting link and join."
            ),
            notification_type="team_call",
            action_url=join_url,
        )
        await db.commit()
    except Exception:
        logger.exception("Could not create the booking-confirmation notification.")
        await db.rollback()

    return TeamCallResponse(
        meeting_id=int(meeting["id"]),
        join_url=join_url,
        start_time=start,
        duration_minutes=int(meeting.get("duration") or zoom_service.TEAM_CALL_MINUTES),
        topic=str(meeting.get("topic") or "Prozpr team call"),
    )


@router.delete("/{meeting_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_team_call(
    meeting_id: int,
    current_user: CurrentUser = Depends(get_effective_user),
):
    try:
        await zoom_service.cancel_team_call_meeting(
            meeting_id, owner_email=_owner_email(current_user)
        )
    except zoom_service.ZoomNotConfigured as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Zoom booking is not configured yet.",
        ) from exc
    except zoom_service.ZoomApiError as exc:
        if exc.status_code == 403:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail=exc.detail
            ) from exc
        logger.error("Zoom cancel-meeting failed: %s", exc.detail)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not cancel the Zoom meeting right now. Please try again.",
        ) from exc
