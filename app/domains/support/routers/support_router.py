"""FastAPI router — `support_router.py`.

Declares HTTP routes, dependencies (auth, user context), and maps request/response
schemas. The support domain records reports in a shared Google Sheet (no DB
table, no local fallback) and emails them to the support inbox — see
``issue_report_service``.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
    status,
)

from app.core.dependencies import CurrentUser, get_effective_user
from app.domains.support.schemas.issue_report import IssueReportResponse
from app.domains.support.services import issue_report_service as svc

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/support", tags=["Support"])

MAX_DESCRIPTION_CHARS = 5000


def _display_name(user: CurrentUser) -> str:
    parts = [user.first_name or "", user.last_name or ""]
    name = " ".join(p for p in parts if p).strip()
    return name or (user.email or "") or str(user.id)


@router.post(
    "/report-issue",
    response_model=IssueReportResponse,
    status_code=status.HTTP_201_CREATED,
)
async def report_issue(
    background_tasks: BackgroundTasks,
    source: str = Form(...),
    source_detail: str | None = Form(None),
    description: str = Form(...),
    screenshot: UploadFile | None = File(None),
    current_user: CurrentUser = Depends(get_effective_user),
):
    source = source.strip()
    source_detail = (source_detail or "").strip() or None
    description = description.strip()
    if source not in svc.ALLOWED_SOURCES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"source must be one of: {', '.join(sorted(svc.ALLOWED_SOURCES))}",
        )
    if source == "Other" and not source_detail:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Please tell us where you saw the issue.",
        )
    if source_detail and len(source_detail) > 100:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Source detail is too long (max 100 characters).",
        )
    if not description:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Please describe the issue.",
        )
    if len(description) > MAX_DESCRIPTION_CHARS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Issue description is too long (max {MAX_DESCRIPTION_CHARS} characters).",
        )

    screenshot_bytes: bytes | None = None
    screenshot_type: str | None = None
    if screenshot is not None and screenshot.filename:
        screenshot_type = (screenshot.content_type or "").lower()
        if screenshot_type not in svc.ALLOWED_IMAGE_TYPES:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Screenshot must be a PNG, JPEG, WEBP, or GIF image.",
            )
        screenshot_bytes = await screenshot.read()
        if len(screenshot_bytes) > svc.MAX_SCREENSHOT_BYTES:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Screenshot is too large (max 5 MB).",
            )

    report_id = uuid.uuid4()
    created_at = datetime.now(timezone.utc)
    user_name = _display_name(current_user)
    user_email = current_user.email or ""
    source_label = f"{source} — {source_detail}" if source_detail else source

    screenshot_name: str | None = None
    if screenshot_bytes and screenshot_type:
        try:
            saved = await asyncio.to_thread(
                svc.save_screenshot, screenshot_bytes, screenshot_type, str(report_id)
            )
            screenshot_name = Path(saved).name
        except OSError:
            logger.exception("Could not save issue screenshot; continuing without it.")

    # Register the row in the Google Sheet — the sole issue register. There is
    # no local fallback, so any failure must surface as a 503 (ask the user to
    # retry) rather than silently dropping the report.
    row_args = (
        created_at,
        user_name,
        user_email,
        source_label,
        description,
        screenshot_name,
    )
    try:
        await asyncio.to_thread(svc.append_to_google_sheet, *row_args)
    except Exception:
        logger.exception("Could not record issue report to the Google Sheet register.")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Could not record the issue right now (issue register unavailable). "
                "Please try again in a moment."
            ),
        )

    # Fire-and-forget: the user gets 201 even if Zoho is down.
    background_tasks.add_task(
        svc.send_issue_email,
        created_at,
        user_name,
        user_email,
        source_label,
        description,
        screenshot_bytes,
        screenshot_type,
    )

    return IssueReportResponse(
        id=report_id,
        source=source,
        source_detail=source_detail,
        description=description,
        has_screenshot=screenshot_name is not None,
        created_at=created_at,
    )
