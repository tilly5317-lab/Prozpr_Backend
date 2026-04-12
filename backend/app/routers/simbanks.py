"""FastAPI router — `simbanks.py`.

Declares HTTP routes, dependencies (auth, DB session, user context), and maps request/response schemas. Delegates work to ``app.services`` and returns appropriate status codes and Pydantic models.
"""


from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import CurrentUser, get_effective_user
from app.schemas.simbanks import (
    DiscoverSimBankAccountsResponse,
    SyncSimBankAccountsRequest,
    SyncSimBankAccountsResponse,
)
from app.services.effective_risk_profile import maybe_recalculate_effective_risk
from app.services.simbanks_service import discover_simbanks_accounts, sync_simbanks_accounts

router = APIRouter(prefix="/simbanks", tags=["SimBanks"])


@router.get("/discover", response_model=DiscoverSimBankAccountsResponse)
async def discover_accounts(
    db: AsyncSession = Depends(get_db),  # noqa: ARG001 - kept for parity/future use
    current_user: CurrentUser = Depends(get_effective_user),
):
    try:
        accounts = await discover_simbanks_accounts(current_user.mobile)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return DiscoverSimBankAccountsResponse(accounts=accounts)


@router.post("/sync", response_model=SyncSimBankAccountsResponse)
async def sync_accounts(
    payload: SyncSimBankAccountsRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    try:
        portfolio, linked_account_ids = await sync_simbanks_accounts(
            db=db,
            user=current_user,
            accepted_account_ref_nos=payload.accepted_account_ref_nos,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    await maybe_recalculate_effective_risk(db, current_user.id, "simbanks_sync")
    await db.commit()

    return SyncSimBankAccountsResponse(
        portfolio_total_value=float(portfolio.total_value),
        portfolio_total_invested=float(portfolio.total_invested),
        portfolio_total_gain_percentage=float(portfolio.total_gain_percentage)
        if portfolio.total_gain_percentage is not None
        else None,
        linked_account_ids=linked_account_ids,
    )

