"""HTTP routes for ``mf_fund_ratings`` (curated rating + dynamic data)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import CurrentUser, get_effective_user
from app.schemas.mf import (
    MfFundRatingCreate,
    MfFundRatingResponse,
    MfFundRatingUpdate,
)
from app.services.mf import fund_rating_service

router = APIRouter(prefix="/fund-ratings", tags=["MF Data"])


@router.get("/by-scheme/{scheme_code}", response_model=MfFundRatingResponse)
async def get_rating_by_scheme(
    scheme_code: str,
    db: AsyncSession = Depends(get_db),
):
    row = await fund_rating_service.get_rating_by_scheme_code(db, scheme_code)
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Fund rating not found")
    return row


@router.get("/by-isin/{isin}", response_model=MfFundRatingResponse)
async def get_rating_by_isin(
    isin: str,
    db: AsyncSession = Depends(get_db),
):
    row = await fund_rating_service.get_rating_by_isin(db, isin)
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Fund rating not found")
    return row


@router.get("/{rating_id}", response_model=MfFundRatingResponse)
async def get_rating(
    rating_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    return await fund_rating_service.get_rating(db, rating_id)


@router.post("/", response_model=MfFundRatingResponse, status_code=status.HTTP_201_CREATED)
async def create_rating(
    payload: MfFundRatingCreate,
    db: AsyncSession = Depends(get_db),
    _user: CurrentUser = Depends(get_effective_user),
):
    return await fund_rating_service.create_rating(db, payload)


@router.patch("/{rating_id}", response_model=MfFundRatingResponse)
async def update_rating(
    rating_id: uuid.UUID,
    payload: MfFundRatingUpdate,
    db: AsyncSession = Depends(get_db),
    _user: CurrentUser = Depends(get_effective_user),
):
    return await fund_rating_service.update_rating(db, rating_id, payload)


@router.delete("/{rating_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rating(
    rating_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _user: CurrentUser = Depends(get_effective_user),
):
    await fund_rating_service.delete_rating(db, rating_id)
