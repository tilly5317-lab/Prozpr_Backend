"""``/cas-uploads`` — the user's statement history.

Read-only. Every upload the user has ever made, newest first, with the figures
that upload produced. The ACTIVE one is what the whole app is showing; the
superseded ones are what used to be shown and are now only history — nothing
here changes which is which (only a CAS ingest does that).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import CurrentUser, get_effective_user
from app.domains.ingestion.models.cas_upload import CasUpload, CasUploadStatus
from app.domains.ingestion.schemas.cas_upload import CasUploadResponse

router = APIRouter(prefix="/cas-uploads", tags=["MF Ingest"])


@router.get("", response_model=list[CasUploadResponse])
async def list_cas_uploads(
    include_failed: bool = False,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    """Every statement this user has uploaded, newest first.

    Failed parses are hidden by default — they carry no figures and exist for
    support and analytics rather than for the user.
    """
    stmt = select(CasUpload).where(CasUpload.user_id == current_user.id)
    if not include_failed:
        stmt = stmt.where(CasUpload.status != CasUploadStatus.FAILED.value)
    stmt = stmt.order_by(CasUpload.seq.desc())
    return list((await db.execute(stmt)).scalars().all())


@router.get("/active", response_model=CasUploadResponse)
async def get_active_cas_upload(
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    """The statement the app is currently reporting on."""
    row = (
        (
            await db.execute(
                select(CasUpload).where(
                    CasUpload.user_id == current_user.id,
                    CasUpload.status == CasUploadStatus.ACTIVE.value,
                )
            )
        )
        .scalars()
        .first()
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No CAS statement has been imported yet.",
        )
    return row


@router.get("/{cas_upload_id}", response_model=CasUploadResponse)
async def get_cas_upload(
    cas_upload_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    row = (
        (
            await db.execute(
                select(CasUpload).where(
                    CasUpload.id == cas_upload_id,
                    CasUpload.user_id == current_user.id,
                )
            )
        )
        .scalars()
        .first()
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Upload not found."
        )
    return row
