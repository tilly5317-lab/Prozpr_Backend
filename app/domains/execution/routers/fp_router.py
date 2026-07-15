"""HTTP routes — FP order execution (KYC gate, setup, lumpsum, SIP, order status)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.core.dependencies import CurrentUser, get_effective_user
from app.domains.execution.schemas.fp_schemas import (
    FpFoliosResponse,
    FpFundSchemesResponse,
    FpInvestmentReport,
    FpKycRequest,
    FpKycResponse,
    FpKycSetupRequest,
    FpKycSetupResponse,
    FpLumpsumRequest,
    FpOrderBatchResponse,
    FpOrderResponse,
    FpRebalanceExecuteRequest,
    FpRedemptionPlanRequest,
    FpRedemptionRequest,
    FpSetupRequest,
    FpSipRequest,
    FpStatusResponse,
    FpSwitchPlanRequest,
    FpSwitchRequest,
)
from app.domains.execution.services import fp_reports_service, fp_service

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


@router.get("/mf-folios", response_model=FpFoliosResponse)
async def mf_folios(
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    """Investor folios / holdings. Calls FP ``GET /v2/mf_folios``; on the sandbox
    that is empty (no order settles → no folio), so it falls back to
    clearly-labelled simulated test data (``simulated=true``), priced with real
    NAVs where available."""
    return await fp_reports_service.build_folios(db, current_user.id)


@router.get("/reports/investment", response_model=FpInvestmentReport)
async def investment_report(
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    """Investment report — holdings summary + per-scheme returns (XIRR/CAGR) +
    capital gains. Built from FP folios if present, else from simulated test
    folios (``simulated=true``)."""
    return await fp_reports_service.build_investment_report(db, current_user.id)


@router.get("/fund-schemes", response_model=FpFundSchemesResponse)
async def fund_schemes(
    verify: bool = False,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    """Active, transactable funds on the FP tenant (what you can actually order).
    On sandbox this is the tenant-enabled ICICI set — any other scheme is
    rejected at order time with "scheme is not available for transaction". Pass
    ``?verify=true`` to confirm each ISIN live against FP's mf_scheme_plans."""
    return await fp_service.list_fund_schemes(db, verify=verify)


@router.post(
    "/kyc/setup",
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


@router.post(
    "/kyc/check", response_model=FpKycResponse, status_code=status.HTTP_201_CREATED
)
async def run_kyc(
    payload: FpKycRequest,
    current_user: CurrentUser = Depends(get_effective_user),
):
    """KYC readiness check (Pre-Verification) — stateless variant. Mirrors FP's
    ``POST /api/kyc/check``."""
    return await fp_service.run_kyc_check(payload)


@router.get("/kyc/check/{pv_id}", response_model=FpKycResponse)
async def kyc_status(
    pv_id: str,
    current_user: CurrentUser = Depends(get_effective_user),
):
    return await fp_service.get_kyc_check(pv_id)


@router.post(
    "/investor-profiles",
    response_model=FpStatusResponse,
    status_code=status.HTTP_201_CREATED,
)
async def setup(
    payload: FpSetupRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    """Manual one-time account setup — runs the FP investor_profiles +
    contacts + bank + mf_investment_accounts chain (the KYC page normally does
    this for you)."""
    account = await fp_service.setup_account(db, current_user, payload)
    return FpStatusResponse(
        configured=get_settings().fp_enabled(),
        account=account,
        kyc_complete=account.kyc_status == "completed",
        ready_to_transact=bool(
            account.kyc_status == "completed" and account.fp_investment_account_id
        ),
    )


# --- Buy (mf_purchases) ---------------------------------------------------
@router.post(
    "/mf-purchases",
    response_model=FpOrderResponse,
    status_code=status.HTTP_201_CREATED,
)
async def mf_purchases(
    payload: FpLumpsumRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    """One-time buy (lumpsum) — FP ``POST /v2/mf_purchases``."""
    return await fp_service.place_lumpsum(
        db, current_user, payload, _client_ip(request)
    )


@router.post(
    "/mf-purchases/from-rebalance",
    response_model=FpOrderBatchResponse,
    status_code=status.HTTP_201_CREATED,
)
async def mf_purchases_from_rebalance(
    request: Request,
    payload: FpRebalanceExecuteRequest | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    """Place FP mf_purchases for the latest rebalancing run's BUY trades (pending
    or retryable-failed), honouring per-trade amount edits from the review UI."""
    orders, failed = await fp_service.execute_rebalance_buys(
        db,
        current_user,
        _client_ip(request),
        amounts=payload.amounts if payload else None,
    )
    return FpOrderBatchResponse(orders=orders, failed=failed)


# --- SIP (mf_purchase_plans) ----------------------------------------------
@router.post(
    "/mf-purchase-plans",
    response_model=FpOrderResponse,
    status_code=status.HTTP_201_CREATED,
)
async def mf_purchase_plans(
    payload: FpSipRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    """Start a SIP — FP ``POST /v2/mf_purchase_plans``."""
    return await fp_service.place_sip(db, current_user, payload, _client_ip(request))


@router.post(
    "/mf-purchase-plans/from-sip-run",
    response_model=FpOrderBatchResponse,
    status_code=status.HTTP_201_CREATED,
)
async def mf_purchase_plans_from_sip_run(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    """Place an FP SIP purchase plan for every fund of the latest SIP run."""
    orders, failed = await fp_service.execute_sip_plan(
        db, current_user, _client_ip(request)
    )
    return FpOrderBatchResponse(orders=orders, failed=failed)


# --- Sell (mf_redemptions) ------------------------------------------------
@router.post(
    "/mf-redemptions",
    response_model=FpOrderResponse,
    status_code=status.HTTP_201_CREATED,
)
async def mf_redemptions(
    payload: FpRedemptionRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    """Sell (redeem) — FP ``POST /v2/mf_redemptions``."""
    return await fp_service.place_redemption(
        db, current_user, payload, _client_ip(request)
    )


# --- Switch (mf_switches) -------------------------------------------------
@router.post(
    "/mf-switches",
    response_model=FpOrderResponse,
    status_code=status.HTTP_201_CREATED,
)
async def mf_switches(
    payload: FpSwitchRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    """Switch scheme->scheme within one AMC — FP ``POST /v2/mf_switches``."""
    return await fp_service.place_switch(
        db, current_user, payload, _client_ip(request)
    )


# --- SWP (mf_redemption_plans) --------------------------------------------
@router.post(
    "/mf-redemption-plans",
    response_model=FpOrderResponse,
    status_code=status.HTTP_201_CREATED,
)
async def mf_redemption_plans(
    payload: FpRedemptionPlanRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    """SWP — systematic withdrawal, FP ``POST /v2/mf_redemption_plans``."""
    return await fp_service.place_redemption_plan(
        db, current_user, payload, _client_ip(request)
    )


# --- STP (mf_switch_plans) ------------------------------------------------
@router.post(
    "/mf-switch-plans",
    response_model=FpOrderResponse,
    status_code=status.HTTP_201_CREATED,
)
async def mf_switch_plans(
    payload: FpSwitchPlanRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    """STP — systematic transfer, FP ``POST /v2/mf_switch_plans``."""
    return await fp_service.place_switch_plan(
        db, current_user, payload, _client_ip(request)
    )


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
