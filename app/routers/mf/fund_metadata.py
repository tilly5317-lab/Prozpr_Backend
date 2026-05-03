"""HTTP routes for ``mf_fund_metadata``."""

from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import CurrentUser, get_effective_user
from app.schemas.mf import MfFundMetadataCreate, MfFundMetadataResponse, MfFundMetadataUpdate
from app.services.mf import fund_metadata_service

router = APIRouter(prefix="/fund-metadata", tags=["MF Data"])


@router.get("/", response_model=list[MfFundMetadataResponse])
async def list_fund_metadata(
    db: AsyncSession = Depends(get_db),
    # _user: CurrentUser = Depends(get_effective_user),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    scheme_code: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
):
    return await fund_metadata_service.list_metadata(
        db, skip=skip, limit=limit, scheme_code=scheme_code, category=category
    )


@router.get("/{metadata_id}", response_model=MfFundMetadataResponse)
async def get_fund_metadata(
    metadata_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _user: CurrentUser = Depends(get_effective_user),
):
    return await fund_metadata_service.get_metadata(db, metadata_id)


@router.post("/", response_model=MfFundMetadataResponse, status_code=status.HTTP_201_CREATED)
async def create_fund_metadata(
    payload: MfFundMetadataCreate,
    db: AsyncSession = Depends(get_db),
    _user: CurrentUser = Depends(get_effective_user),
):
    return await fund_metadata_service.create_metadata(db, payload)


@router.patch("/{metadata_id}", response_model=MfFundMetadataResponse)
async def update_fund_metadata(
    metadata_id: uuid.UUID,
    payload: MfFundMetadataUpdate,
    db: AsyncSession = Depends(get_db),
    _user: CurrentUser = Depends(get_effective_user),
):
    return await fund_metadata_service.update_metadata(db, metadata_id, payload)


@router.delete("/{metadata_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_fund_metadata(
    metadata_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _user: CurrentUser = Depends(get_effective_user),
):
    await fund_metadata_service.delete_metadata(db, metadata_id)
