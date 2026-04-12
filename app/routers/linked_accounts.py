"""FastAPI router — `linked_accounts.py`.

Declares HTTP routes, dependencies (auth, DB session, user context), and maps request/response schemas. Delegates work to ``app.services`` and returns appropriate status codes and Pydantic models.
"""


from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import CurrentUser, get_effective_user
from app.models.linked_account import LinkedAccount, LinkedAccountStatus, LinkedAccountType
from app.schemas.linked_account import LinkAccountListResponse, LinkAccountRequest, LinkAccountResponse
from app.utils.security import get_fernet

router = APIRouter(prefix="/linked-accounts", tags=["Linked Accounts"])


@router.get("/", response_model=LinkAccountListResponse)
async def list_accounts(
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    stmt = (
        select(LinkedAccount)
        .where(LinkedAccount.user_id == current_user.id)
        .order_by(LinkedAccount.created_at.desc())
    )
    result = await db.execute(stmt)
    accounts = [LinkAccountResponse.model_validate(a) for a in result.scalars().all()]
    return LinkAccountListResponse(accounts=accounts)


@router.post("/", response_model=LinkAccountResponse, status_code=status.HTTP_201_CREATED)
async def link_account(
    payload: LinkAccountRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    try:
        fernet = get_fernet()
    except RuntimeError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e)
        ) from e

    encrypted_token = fernet.encrypt(b"pending").decode("utf-8")

    account = LinkedAccount(
        user_id=current_user.id,
        account_type=LinkedAccountType(payload.account_type),
        provider_name=payload.provider_name,
        status=LinkedAccountStatus.pending,
        encrypted_access_token=encrypted_token,
    )
    db.add(account)
    await db.commit()
    await db.refresh(account)
    return LinkAccountResponse.model_validate(account)


@router.delete("/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
async def unlink_account(
    account_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    stmt = select(LinkedAccount).where(
        LinkedAccount.id == account_id, LinkedAccount.user_id == current_user.id
    )
    account = (await db.execute(stmt)).scalar_one_or_none()
    if not account:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")

    await db.delete(account)
    await db.commit()
