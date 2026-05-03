"""HTTP routes for ``mf_fund_metadata``."""

from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import CurrentUser, get_effective_user
from app.schemas.mf import (
    MfFundInvestorDetailResponse,
    MfFundMetadataCreate,
    MfFundMetadataListItem,
    MfFundMetadataResponse,
    MfFundMetadataSearchResponse,
    MfFundMetadataUpdate,
)
from app.services.mf import fund_metadata_service, mf_investor_detail_service

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


@router.get("/search", response_model=MfFundMetadataSearchResponse)
async def search_fund_metadata(
    db: AsyncSession = Depends(get_db),
    q: Optional[str] = Query(None, description="Free-text search across scheme name, AMC, scheme code and ISIN"),
    category: Optional[str] = Query(None),
    sub_category: Optional[str] = Query(None),
    asset_class: Optional[str] = Query(None),
    amc_name: Optional[str] = Query(None),
    active_only: bool = Query(True),
    limit: int = Query(20, ge=1, le=50),
    offset: int = Query(0, ge=0),
):
    """Paginated search over the AMFI universe stored in ``mf_fund_metadata``.

    Drives the Discover page search bar and the temporary "Explore all funds"
    sheet. Returns a slim response shape so each page stays small enough for
    smooth infinite scroll on mobile clients.
    """
    rows, total = await fund_metadata_service.search_metadata(
        db,
        q=q,
        category=category,
        sub_category=sub_category,
        asset_class=asset_class,
        amc_name=amc_name,
        active_only=active_only,
        limit=limit,
        offset=offset,
    )
    items = [MfFundMetadataListItem.model_validate(r) for r in rows]
    return MfFundMetadataSearchResponse(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
        has_more=(offset + len(items)) < total,
    )


@router.get(
    "/{metadata_id}/investor-detail",
    response_model=MfFundInvestorDetailResponse,
    summary="Fund detail for investors (NAV-based returns + chart)",
    description=(
        "Returns scheme facts and performance derived from stored NAV history (rolling windows). "
        "Also echoes headline returns from metadata when present. Public — no auth required."
    ),
)
async def get_fund_investor_detail(
    metadata_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    return await mf_investor_detail_service.build_investor_detail(db, metadata_id)


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
