"""HTTP routes for AA import batches and nested summary / transaction rows."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import CurrentUser, get_effective_user
from app.schemas.mf import (
    MfAaImportCreate,
    MfAaImportResponse,
    MfAaImportUpdate,
    MfAaSummaryCreate,
    MfAaSummaryResponse,
    MfAaSummaryUpdate,
    MfAaTransactionCreate,
    MfAaTransactionResponse,
    MfAaTransactionUpdate,
)
from app.services.mf import aa_import_service

router = APIRouter(prefix="/aa-imports", tags=["MF Data"])

summaries_router = APIRouter(prefix="/{import_id}/summaries", tags=["MF Data"])
aa_txns_router = APIRouter(prefix="/{import_id}/aa-transactions", tags=["MF Data"])


# --- Import root ---


@router.get("/", response_model=list[MfAaImportResponse])
async def list_aa_imports(
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    return await aa_import_service.list_imports(db, current_user.id, skip=skip, limit=limit)


@router.get("/{import_id}", response_model=MfAaImportResponse)
async def get_aa_import(
    import_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    return await aa_import_service.get_import(db, import_id, current_user.id)


@router.post("/", response_model=MfAaImportResponse, status_code=status.HTTP_201_CREATED)
async def create_aa_import(
    payload: MfAaImportCreate,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    return await aa_import_service.create_import(db, current_user.id, payload)


@router.patch("/{import_id}", response_model=MfAaImportResponse)
async def update_aa_import(
    import_id: uuid.UUID,
    payload: MfAaImportUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    return await aa_import_service.update_import(db, import_id, current_user.id, payload)


@router.delete("/{import_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_aa_import(
    import_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    await aa_import_service.delete_import(db, import_id, current_user.id)


# --- Nested summaries ---


@summaries_router.get("/", response_model=list[MfAaSummaryResponse])
async def list_aa_summaries(
    import_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    return await aa_import_service.list_summaries(db, import_id, current_user.id, skip=skip, limit=limit)


@summaries_router.get("/{summary_id}", response_model=MfAaSummaryResponse)
async def get_aa_summary(
    import_id: uuid.UUID,
    summary_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    return await aa_import_service.get_summary(db, import_id, summary_id, current_user.id)


@summaries_router.post("/", response_model=MfAaSummaryResponse, status_code=status.HTTP_201_CREATED)
async def create_aa_summary(
    import_id: uuid.UUID,
    payload: MfAaSummaryCreate,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    return await aa_import_service.create_summary(db, import_id, current_user.id, payload)


@summaries_router.patch("/{summary_id}", response_model=MfAaSummaryResponse)
async def update_aa_summary(
    import_id: uuid.UUID,
    summary_id: uuid.UUID,
    payload: MfAaSummaryUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    return await aa_import_service.update_summary(db, import_id, summary_id, current_user.id, payload)


@summaries_router.delete("/{summary_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_aa_summary(
    import_id: uuid.UUID,
    summary_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    await aa_import_service.delete_summary(db, import_id, summary_id, current_user.id)


# --- Nested AA transaction CSV rows ---


@aa_txns_router.get("/", response_model=list[MfAaTransactionResponse])
async def list_aa_import_transactions(
    import_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    return await aa_import_service.list_aa_transactions(
        db, import_id, current_user.id, skip=skip, limit=limit
    )


@aa_txns_router.get("/{txn_id}", response_model=MfAaTransactionResponse)
async def get_aa_import_transaction(
    import_id: uuid.UUID,
    txn_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    return await aa_import_service.get_aa_transaction(db, import_id, txn_id, current_user.id)


@aa_txns_router.post("/", response_model=MfAaTransactionResponse, status_code=status.HTTP_201_CREATED)
async def create_aa_import_transaction(
    import_id: uuid.UUID,
    payload: MfAaTransactionCreate,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    return await aa_import_service.create_aa_transaction(db, import_id, current_user.id, payload)


@aa_txns_router.patch("/{txn_id}", response_model=MfAaTransactionResponse)
async def update_aa_import_transaction(
    import_id: uuid.UUID,
    txn_id: uuid.UUID,
    payload: MfAaTransactionUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    return await aa_import_service.update_aa_transaction(
        db, import_id, txn_id, current_user.id, payload
    )


@aa_txns_router.delete("/{txn_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_aa_import_transaction(
    import_id: uuid.UUID,
    txn_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    await aa_import_service.delete_aa_transaction(db, import_id, txn_id, current_user.id)


router.include_router(summaries_router)
router.include_router(aa_txns_router)
