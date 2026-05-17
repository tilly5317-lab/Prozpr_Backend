"""HTTP routes for ``user_investment_lists``."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import CurrentUser, get_effective_user
from app.schemas.mf import (
    UserInvestmentListCreate,
    UserInvestmentListResponse,
    UserInvestmentListUpdate,
)
from app.services.mf import user_investment_list_service

router = APIRouter(prefix="/user-investment-lists", tags=["MF Data"])


@router.get("/", response_model=list[UserInvestmentListResponse])
async def list_user_investment_lists(
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    return await user_investment_list_service.list_lists(db, current_user.id)


@router.get("/{list_id}", response_model=UserInvestmentListResponse)
async def get_user_investment_list(
    list_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    return await user_investment_list_service.get_list(db, list_id, current_user.id)


@router.post("/", response_model=UserInvestmentListResponse, status_code=status.HTTP_201_CREATED)
async def create_user_investment_list(
    payload: UserInvestmentListCreate,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    return await user_investment_list_service.create_list(db, current_user.id, payload)


@router.patch("/{list_id}", response_model=UserInvestmentListResponse)
async def update_user_investment_list(
    list_id: uuid.UUID,
    payload: UserInvestmentListUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    return await user_investment_list_service.update_list(db, list_id, current_user.id, payload)


@router.delete("/{list_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user_investment_list(
    list_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    await user_investment_list_service.delete_list(db, list_id, current_user.id)
