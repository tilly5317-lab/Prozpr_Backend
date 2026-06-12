"""FastAPI router — `support_router.py`.

Declares HTTP routes, dependencies (auth, DB session, user context), and maps request/response schemas. Delegates work to the support services and returns appropriate status codes and Pydantic models.
"""


from __future__ import annotations

import asyncio
import logging

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
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import CurrentUser, get_effective_user
from app.domains.support.models.issue_report import IssueReport
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
    db: AsyncSession = Depends(get_db),
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

    report = IssueReport(
        user_id=current_user.id,
        user_name=_display_name(current_user),
        user_email=current_user.email,
        source=source,
        source_detail=source_detail,
        description=description,
    )
    db.add(report)
    await db.flush()  # assigns report.id for the screenshot filename

    if screenshot_bytes and screenshot_type:
        try:
            report.screenshot_path = await asyncio.to_thread(
                svc.save_screenshot, screenshot_bytes, screenshot_type, str(report.id)
            )
        except OSError:
            logger.exception("Could not save issue screenshot; continuing without it.")

    await db.commit()
    await db.refresh(report)

    # Fire-and-forget: the user gets 201 even if Zoho is down.
    background_tasks.add_task(
        svc.send_issue_email,
        report.created_at,
        report.user_name or "",
        report.user_email or "",
        report.source,
        report.source_detail,
        report.description,
        screenshot_bytes,
        screenshot_type,
    )

    return IssueReportResponse(
        id=report.id,
        source=report.source,
        source_detail=report.source_detail,
        description=report.description,
        has_screenshot=report.screenshot_path is not None,
        created_at=report.created_at,
    )
