"""HTTP routes for ``mf_transactions``."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import CurrentUser, get_effective_user
from app.schemas.mf import MfTransactionCreate, MfTransactionResponse, MfTransactionUpdate
from app.services.mf import transaction_service

router = APIRouter(prefix="/transactions", tags=["MF Data"])


@router.get("/", response_model=list[MfTransactionResponse])
async def list_mf_transactions(
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    scheme_code: Optional[str] = Query(None),
    transaction_date_from: Optional[date] = Query(None),
    transaction_date_to: Optional[date] = Query(None),
):
    return await transaction_service.list_transactions(
        db,
        current_user.id,
        skip=skip,
        limit=limit,
        scheme_code=scheme_code,
        transaction_date_from=transaction_date_from,
        transaction_date_to=transaction_date_to,
    )


@router.get("/{txn_id}", response_model=MfTransactionResponse)
async def get_mf_transaction(
    txn_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    return await transaction_service.get_transaction(db, txn_id, current_user.id)


@router.post("/", response_model=MfTransactionResponse, status_code=status.HTTP_201_CREATED)
async def create_mf_transaction(
    payload: MfTransactionCreate,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    return await transaction_service.create_transaction(db, current_user.id, payload)


@router.patch("/{txn_id}", response_model=MfTransactionResponse)
async def update_mf_transaction(
    txn_id: uuid.UUID,
    payload: MfTransactionUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    return await transaction_service.update_transaction(db, txn_id, current_user.id, payload)


@router.delete("/{txn_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_mf_transaction(
    txn_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    await transaction_service.delete_transaction(db, txn_id, current_user.id)
