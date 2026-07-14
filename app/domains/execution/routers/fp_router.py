"""HTTP routes — FP order execution (KYC gate, setup, lumpsum, SIP, order status)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.core.dependencies import CurrentUser, get_effective_user
from app.domains.execution.schemas.fp_schemas import (
    FpKycRequest,
    FpKycResponse,
    FpKycSetupRequest,
    FpKycSetupResponse,
    FpLumpsumRequest,
    FpOrderBatchResponse,
    FpOrderResponse,
    FpRebalanceExecuteRequest,
    FpSetupRequest,
    FpSipRequest,
    FpStatusResponse,
)
from app.domains.execution.services import fp_service

router = APIRouter(prefix="/fp", tags=["Fintech Primitives"])

_SANDBOX_IP = "1.2.3.4"


def _client_ip(request: Request) -> str:
    if request.client and request.client.host:
        return request.client.host
    return _SANDBOX_IP


@router.get("/status", response_model=FpStatusResponse)
async def fp_status(
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    """The invest pages' gate: is FP configured, does the user have an account
    row, is KYC complete, can they transact."""
    settings = get_settings()
    account = await fp_service.get_account(db, current_user.id)
    kyc_complete = bool(account and account.kyc_status == "completed")
    return FpStatusResponse(
        configured=settings.fp_enabled(),
        account=account,
        kyc_complete=kyc_complete,
        ready_to_transact=bool(
            kyc_complete and account and account.fp_investment_account_id
        ),
    )


@router.post(
    "/kyc-setup",
    response_model=FpKycSetupResponse,
    status_code=status.HTTP_201_CREATED,
)
async def kyc_setup(
    payload: FpKycSetupRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    """The KYC page's single call: PAN in; name / DOB from the user's identity
    on our backend. Runs Pre-Verification and, once complete, the full FP
    account setup. Idempotent — call again (PAN optional) to re-poll."""
    return await fp_service.run_kyc_and_setup(db, current_user, payload.pan)


@router.post("/kyc", response_model=FpKycResponse, status_code=status.HTTP_201_CREATED)
async def run_kyc(
    payload: FpKycRequest,
    current_user: CurrentUser = Depends(get_effective_user),
):
    """KYC readiness check (Pre-Verification) — stateless variant."""
    return await fp_service.run_kyc_check(payload)


@router.get("/kyc/{pv_id}", response_model=FpKycResponse)
async def kyc_status(
    pv_id: str,
    current_user: CurrentUser = Depends(get_effective_user),
):
    return await fp_service.get_kyc_check(pv_id)


@router.post(
    "/setup", response_model=FpStatusResponse, status_code=status.HTTP_201_CREATED
)
async def setup(
    payload: FpSetupRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    """Manual one-time account setup (KYC page normally does this for you)."""
    account = await fp_service.setup_account(db, current_user, payload)
    return FpStatusResponse(
        configured=get_settings().fp_enabled(),
        account=account,
        kyc_complete=account.kyc_status == "completed",
        ready_to_transact=bool(
            account.kyc_status == "completed" and account.fp_investment_account_id
        ),
    )


@router.post(
    "/orders/lumpsum",
    response_model=FpOrderResponse,
    status_code=status.HTTP_201_CREATED,
)
async def lumpsum(
    payload: FpLumpsumRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    return await fp_service.place_lumpsum(
        db, current_user, payload, _client_ip(request)
    )


@router.post(
    "/orders/sip", response_model=FpOrderResponse, status_code=status.HTTP_201_CREATED
)
async def sip(
    payload: FpSipRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    return await fp_service.place_sip(db, current_user, payload, _client_ip(request))


@router.post(
    "/orders/execute-sip-plan",
    response_model=FpOrderBatchResponse,
    status_code=status.HTTP_201_CREATED,
)
async def execute_sip_plan(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    """Place an FP SIP purchase plan for every fund of the latest SIP plan."""
    orders, failed = await fp_service.execute_sip_plan(
        db, current_user, _client_ip(request)
    )
    return FpOrderBatchResponse(orders=orders, failed=failed)


@router.post(
    "/orders/execute-rebalance",
    response_model=FpOrderBatchResponse,
    status_code=status.HTTP_201_CREATED,
)
async def execute_rebalance(
    request: Request,
    payload: FpRebalanceExecuteRequest | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    """Place FP lumpsums for the latest rebalancing run's BUY trades (pending or
    retryable-failed), honouring per-trade amount edits from the review UI."""
    orders, failed = await fp_service.execute_rebalance_buys(
        db,
        current_user,
        _client_ip(request),
        amounts=payload.amounts if payload else None,
    )
    return FpOrderBatchResponse(orders=orders, failed=failed)


@router.get("/orders", response_model=list[FpOrderResponse])
async def orders(
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    return await fp_service.list_orders(db, current_user.id)


@router.post("/orders/{order_id}/refresh", response_model=FpOrderResponse)
async def refresh(
    order_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    return await fp_service.refresh_order(db, current_user.id, order_id)
