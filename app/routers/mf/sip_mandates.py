"""HTTP routes for ``mf_sip_mandates``."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import CurrentUser, get_effective_user
from app.schemas.mf import MfSipMandateCreate, MfSipMandateResponse, MfSipMandateUpdate
from app.services.mf import sip_mandate_service

router = APIRouter(prefix="/sip-mandates", tags=["MF Data"])


@router.get("/", response_model=list[MfSipMandateResponse])
async def list_sip_mandates(
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    return await sip_mandate_service.list_mandates(db, current_user.id, skip=skip, limit=limit)


@router.get("/{mandate_id}", response_model=MfSipMandateResponse)
async def get_sip_mandate(
    mandate_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    return await sip_mandate_service.get_mandate(db, mandate_id, current_user.id)


@router.post("/", response_model=MfSipMandateResponse, status_code=status.HTTP_201_CREATED)
async def create_sip_mandate(
    payload: MfSipMandateCreate,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    return await sip_mandate_service.create_mandate(db, current_user.id, payload)


@router.patch("/{mandate_id}", response_model=MfSipMandateResponse)
async def update_sip_mandate(
    mandate_id: uuid.UUID,
    payload: MfSipMandateUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    return await sip_mandate_service.update_mandate(db, mandate_id, current_user.id, payload)


@router.delete("/{mandate_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_sip_mandate(
    mandate_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    await sip_mandate_service.delete_mandate(db, mandate_id, current_user.id)
