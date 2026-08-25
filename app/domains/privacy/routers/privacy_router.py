"""HTTP routes — DPDP data-principal rights.

Notice, consent (grant and withdraw), access/export, erasure, and grievance.
Every route is scoped to the calling user; there is no admin surface here on
purpose — the rights belong to the person, not to an operator.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import CurrentUser, get_current_user
from app.domains.identity.models.user import User
from app.domains.privacy.models.consent import (
    OPTIONAL_PURPOSES,
    ConsentPurpose,
    Grievance,
)
from app.domains.privacy.schemas.privacy import (
    ConsentStateResponse,
    ConsentUpdateRequest,
    DataExportResponse,
    ErasureResponse,
    GrievanceRequest,
    GrievanceResponse,
    PrivacyNoticeResponse,
    PurposeNotice,
)
from app.domains.privacy.services import consent_service, erasure_service, export_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/privacy", tags=["Privacy & DPDP"])


def _purpose_rows(latest: dict) -> list[PurposeNotice]:
    out: list[PurposeNotice] = []
    for purpose in ConsentPurpose:
        note = consent_service.PURPOSE_NOTICE[purpose]
        row = latest.get(purpose)
        out.append(
            PurposeNotice(
                purpose=purpose,
                title=note["title"],
                detail=note["detail"],
                necessary=purpose not in OPTIONAL_PURPOSES,
                granted=row.granted if row else None,
                recorded_at=row.recorded_at if row else None,
                policy_version=row.policy_version if row else None,
            )
        )
    return out


@router.get("/notice", response_model=PrivacyNoticeResponse)
async def privacy_notice(
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """The itemised notice, with this user's current position against each purpose."""
    latest = await consent_service.current_consents(db, current_user.id)
    return PrivacyNoticeResponse(
        policy_version=consent_service.CURRENT_POLICY_VERSION,
        content_hash=consent_service.notice_hash(),
        purposes=_purpose_rows(latest),
        grievance_contact=None,
    )


@router.get("/consent", response_model=ConsentStateResponse)
async def get_consent(
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    latest = await consent_service.current_consents(db, current_user.id)
    return ConsentStateResponse(
        policy_version=consent_service.CURRENT_POLICY_VERSION,
        purposes=_purpose_rows(latest),
    )


@router.post("/consent", response_model=ConsentStateResponse)
async def update_consent(
    payload: ConsentUpdateRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Grant or withdraw consent. Withdrawal is this same call with ``granted: false``.

    Necessary purposes are accepted but ignored for withdrawal — the way to
    refuse those is ``DELETE /privacy/account``, and pretending a toggle can
    switch them off would misrepresent what the product does.
    """
    await consent_service.ensure_current_policy_version(db)
    ip = request.client.host if request.client else None
    ua = request.headers.get("user-agent")

    for item in payload.consents:
        if item.purpose not in OPTIONAL_PURPOSES and not item.granted:
            logger.info("Ignoring withdrawal of a necessary purpose: %s", item.purpose)
            continue
        await consent_service.record_consent(
            db,
            user_id=current_user.id,
            purpose=item.purpose,
            granted=item.granted,
            source="web",
            ip=ip,
            user_agent=ua,
        )
    await db.commit()

    latest = await consent_service.current_consents(db, current_user.id)
    return ConsentStateResponse(
        policy_version=consent_service.CURRENT_POLICY_VERSION,
        purposes=_purpose_rows(latest),
    )


@router.get("/export", response_model=DataExportResponse)
async def export_my_data(
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """The right of access: every row we hold about the caller, plus who it was shared with."""
    doc = await export_service.build_export(db, current_user.id)
    doc["statement_archive"] = await export_service.statement_archive(
        db, current_user.id
    )
    return DataExportResponse(**doc)


@router.post("/grievance", response_model=GrievanceResponse, status_code=status.HTTP_201_CREATED)
async def raise_grievance(
    payload: GrievanceRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """File a data-protection complaint.

    Stored in the database rather than the issue-report Google Sheet, so that an
    erasure request can actually reach it later.
    """
    row = Grievance(
        user_id=current_user.id,
        category=payload.category,
        message=payload.message,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return GrievanceResponse(
        id=row.id,
        category=row.category,
        status=row.status.value,
        created_at=row.created_at,
    )


@router.delete("/account", response_model=ErasureResponse)
async def erase_my_account(
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """The right to erasure.

    Identity is destroyed immediately and the session stops working on the next
    request; the rows themselves go when the grace window expires. The response
    says exactly that rather than claiming the data is already gone.
    """
    user = (
        await db.execute(select(User).where(User.id == current_user.id))
    ).scalar_one()
    due = await erasure_service.request_erasure(db, user)
    await db.commit()

    return ErasureResponse(
        deleted_at=user.deleted_at,
        purge_scheduled_for=due,
        grace_days=erasure_service.GRACE_DAYS,
        detail=(
            "Your account is closed and you have been signed out. Your data is "
            f"permanently deleted after {erasure_service.GRACE_DAYS} days. "
            "Contact support within that window if this was a mistake."
        ),
    )
