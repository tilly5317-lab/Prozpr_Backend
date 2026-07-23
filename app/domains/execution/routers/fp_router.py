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


# ===========================================================================
# Sandbox QUEST endpoints — DEV TOOLS for FP_Sandbox_Quest_Map.html
# (unauthenticated; one named route per journey step so the uvicorn access
# log reads like the flow itself). Every call funnels through FpClient, which
# emits the ">>> FP-API >>>" log line. Hard-gated to the FP *sandbox* host —
# these can never touch production, and they refuse non-sandbox base URLs.
# ===========================================================================

from typing import Any, Literal  # noqa: E402

from fastapi import Body, HTTPException  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from app.domains.execution.services.fp_client import (  # noqa: E402
    FpClient,
    FpError,
    get_fp_client,
    get_fp_preverify_client,
)

_proxy_clients: dict[str, FpClient] = {}


class FpProxyResponse(BaseModel):
    """Uniform envelope: FP's HTTP status + FP's body verbatim (including FP
    error bodies), so the Quest Map can render exactly what FP said."""

    status: int
    body: Any = None


def _sandbox_only() -> None:
    if "s.finprim.com" not in get_settings().get_fp_base_url():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Sandbox quest endpoints only run against the FP sandbox host.",
        )


def _quest_client(kind: str) -> FpClient:
    """Module-level singletons so the 30-min token is reused across calls
    (fresh clients per call re-mint every time and trip FP's 429 limiter)."""
    client = _proxy_clients.get(kind)
    if client is None:
        client = get_fp_client() if kind == "order" else get_fp_preverify_client()
        _proxy_clients[kind] = client
    return client


async def _fp_pass(
    kind: str,
    method: str,
    path: str,
    body: Any = None,
    params: dict | None = None,
) -> FpProxyResponse:
    _sandbox_only()
    client = _quest_client(kind)
    try:
        resp = await client.request(method, path, json=body, params=params)
    except FpError as exc:
        return FpProxyResponse(status=exc.status_code or 502, body=exc.body)
    return FpProxyResponse(status=200, body=resp)


class FpQuestTokenRequest(BaseModel):
    tenant: Literal["order", "kyc"] = "order"


@router.post("/sandbox/token", response_model=FpProxyResponse)
async def quest_token(payload: FpQuestTokenRequest) -> FpProxyResponse:
    """Step 1 — mint/refresh the OAuth token for one of the two systems:
    ``order`` = Fintech Primitives order tenant (prozpr), ``kyc`` = Cybrilla
    pre-verification tenant (cybrillarta). Token stays server-side."""
    _sandbox_only()
    client = _quest_client(payload.tenant)
    try:
        token = await client._ensure_token()  # noqa: SLF001 — dev tool
    except FpError as exc:
        return FpProxyResponse(status=exc.status_code or 502, body={"error": str(exc)})
    return FpProxyResponse(
        status=200,
        body={"tenant": payload.tenant, "token_preview": token[:24] + "..."},
    )


@router.post("/sandbox/kyc-preverification", response_model=FpProxyResponse)
async def quest_kyc_preverification(body: Any = Body(...)) -> FpProxyResponse:
    """Step 2 — KYC readiness check on the Cybrilla POA service
    (``POST /poa/pre_verifications``, tenant cybrillarta)."""
    return await _fp_pass("kyc", "POST", "/poa/pre_verifications", body)


@router.get("/sandbox/kyc-preverification/{pv_id}", response_model=FpProxyResponse)
async def quest_kyc_preverification_poll(pv_id: str) -> FpProxyResponse:
    return await _fp_pass("kyc", "GET", "/poa/pre_verifications/" + pv_id)


@router.post("/sandbox/investor-profile", response_model=FpProxyResponse)
async def quest_investor_profile(body: Any = Body(...)) -> FpProxyResponse:
    """Step 3 — CREATE the investor on FP (``POST /v2/investor_profiles``)."""
    return await _fp_pass("order", "POST", "/v2/investor_profiles", body)


@router.post("/sandbox/email-address", response_model=FpProxyResponse)
async def quest_email_address(body: Any = Body(...)) -> FpProxyResponse:
    return await _fp_pass("order", "POST", "/v2/email_addresses", body)


@router.post("/sandbox/phone-number", response_model=FpProxyResponse)
async def quest_phone_number(body: Any = Body(...)) -> FpProxyResponse:
    return await _fp_pass("order", "POST", "/v2/phone_numbers", body)


@router.post("/sandbox/address", response_model=FpProxyResponse)
async def quest_address(body: Any = Body(...)) -> FpProxyResponse:
    """Step 4c — postal address. ``nature`` (residential/...) is MANDATORY or
    every later order fails at the gateway."""
    return await _fp_pass("order", "POST", "/v2/addresses", body)


@router.post("/sandbox/bank-account", response_model=FpProxyResponse)
async def quest_bank_account(body: Any = Body(...)) -> FpProxyResponse:
    """Step 5 — link the bank account (sandbox rule: a/c ending 11XX verifies)."""
    return await _fp_pass("order", "POST", "/v2/bank_accounts", body)


@router.post("/sandbox/mf-investment-account", response_model=FpProxyResponse)
async def quest_mf_investment_account(body: Any = Body(...)) -> FpProxyResponse:
    """Step 6 — open the MF investment account; ``folio_defaults`` carries the
    reference IDs minted in steps 4-5."""
    return await _fp_pass("order", "POST", "/v2/mf_investment_accounts", body)


@router.get("/sandbox/scheme/{isin}", response_model=FpProxyResponse)
async def quest_scheme(isin: str) -> FpProxyResponse:
    """Step 7 — confirm the ISIN is transactable on the sandbox (ABSL + ICICI
    only). ``expand`` is required by FP."""
    return await _fp_pass(
        "order",
        "GET",
        "/v2/mf_scheme_plans/cybrillapoa/" + isin,
        params={"expand": ["mf_scheme", "mf_fund"]},
    )


@router.post("/sandbox/mf-purchase", response_model=FpProxyResponse)
async def quest_mf_purchase(body: Any = Body(...)) -> FpProxyResponse:
    """Step 8 — place the lumpsum BUY. Consent must ride inline (sandbox blocks
    all updates); amount ending 0 is destined to settle, ending 1 to fail."""
    return await _fp_pass("order", "POST", "/v2/mf_purchases", body)


@router.get("/sandbox/mf-purchase/{order_id}", response_model=FpProxyResponse)
async def quest_mf_purchase_poll(order_id: str) -> FpProxyResponse:
    return await _fp_pass("order", "GET", "/v2/mf_purchases/" + order_id)


@router.post("/sandbox/payment", response_model=FpProxyResponse)
async def quest_payment(body: Any = Body(...)) -> FpProxyResponse:
    """Step 9 — lodge the payment on the payment-gateway realm
    (``POST /api/pg/payments/netbanking``; numeric old_ids)."""
    return await _fp_pass("order", "POST", "/api/pg/payments/netbanking", body)


@router.get("/sandbox/payment/{payment_id}", response_model=FpProxyResponse)
async def quest_payment_poll(payment_id: int) -> FpProxyResponse:
    return await _fp_pass("order", "GET", "/api/pg/payments/" + str(payment_id))


@router.get("/sandbox/orders", response_model=FpProxyResponse)
async def quest_orders() -> FpProxyResponse:
    """Order history — every purchase on the tenant with its live state
    (pending / failed / successful), for the Hall of Records panel."""
    return await _fp_pass("order", "GET", "/v2/mf_purchases")


@router.get("/sandbox/mf-folios", response_model=FpProxyResponse)
async def quest_mf_folios(mf_investment_account: str | None = None) -> FpProxyResponse:
    params = (
        {"mf_investment_account": mf_investment_account}
        if mf_investment_account
        else None
    )
    return await _fp_pass("order", "GET", "/v2/mf_folios", params=params)


@router.post("/sandbox/kyc-form", response_model=FpProxyResponse)
async def quest_kyc_form(body: Any = Body(...)) -> FpProxyResponse:
    """Step 2b — SUBMIT a fresh/modify KYC (``POST /poa/kyc_forms``, Cybrilla
    POA tenant). Note: our sandbox partner is not yet provisioned for
    kyc_forms — FP returns 403 "Partner not allowed" until they enable it
    (their 2026-07 mail offered the credentials); the route passes FP's
    answer through verbatim either way."""
    return await _fp_pass("kyc", "POST", "/poa/kyc_forms", body)


@router.get("/sandbox/kyc-form/{form_id}", response_model=FpProxyResponse)
async def quest_kyc_form_poll(form_id: str) -> FpProxyResponse:
    return await _fp_pass("kyc", "GET", "/poa/kyc_forms/" + form_id)


_ACTIVE_ORDER_STATES = {"under_review", "pending", "confirmed", "submitted"}


@router.get("/sandbox/orders-filtered", response_model=FpProxyResponse)
async def quest_orders_filtered(state: str | None = None) -> FpProxyResponse:
    """Order history with an optional state filter: ``state=active`` (in
    flight: under_review/pending/confirmed/submitted), or an exact FP state
    (``pending``/``successful``/``failed``/...). Omit for everything."""
    resp = await _fp_pass("order", "GET", "/v2/mf_purchases")
    if resp.status != 200 or not isinstance(resp.body, dict) or state is None:
        return resp
    rows = resp.body.get("data") or []
    want = state.strip().lower()
    if want == "active":
        rows = [o for o in rows if o.get("state") in _ACTIVE_ORDER_STATES]
    else:
        rows = [o for o in rows if o.get("state") == want]
    return FpProxyResponse(status=200, body={"object": "list", "filter": want, "data": rows})


# ===========================================================================
# Sandbox DAILY-ACTIVITY endpoints — power FP_Daily_Activities.html.
# Same rules as the quest endpoints above: unauthenticated dev tools,
# sandbox-host-gated, every FP call logged via FpClient ("FP-API" lines).
# ===========================================================================


async def _fp_get(kind: str, path: str, params: dict | None = None) -> Any:
    """Raw GET for composite endpoints; returns FP's body or None on error."""
    client = _quest_client(kind)
    try:
        return await client.request("GET", path, params=params)
    except FpError:
        return None


# ---- daily transactions (create) ------------------------------------------


@router.post("/sandbox/sip-plan", response_model=FpProxyResponse)
async def daily_sip_plan(body: Any = Body(...)) -> FpProxyResponse:
    """Start a monthly SIP (``POST /v2/mf_purchase_plans``). Gotcha (live-
    verified): ``consent.email``/``mobile`` must MATCH the investment
    account's folio_defaults contacts or FP 400s."""
    return await _fp_pass("order", "POST", "/v2/mf_purchase_plans", body)


@router.get("/sandbox/sip-plan/{plan_id}", response_model=FpProxyResponse)
async def daily_sip_plan_poll(plan_id: str) -> FpProxyResponse:
    return await _fp_pass("order", "GET", "/v2/mf_purchase_plans/" + plan_id)


@router.post("/sandbox/redemption", response_model=FpProxyResponse)
async def daily_redemption(body: Any = Body(...)) -> FpProxyResponse:
    """Sell (``POST /v2/mf_redemptions``). Requires ``folio_number`` — a folio
    only exists after a purchase has SETTLED, so this 400s until then."""
    return await _fp_pass("order", "POST", "/v2/mf_redemptions", body)


@router.post("/sandbox/switch", response_model=FpProxyResponse)
async def daily_switch(body: Any = Body(...)) -> FpProxyResponse:
    """Switch scheme->scheme within one AMC (``POST /v2/mf_switches``)."""
    return await _fp_pass("order", "POST", "/v2/mf_switches", body)


@router.post("/sandbox/swp", response_model=FpProxyResponse)
async def daily_swp(body: Any = Body(...)) -> FpProxyResponse:
    """Systematic withdrawal plan (``POST /v2/mf_redemption_plans``)."""
    return await _fp_pass("order", "POST", "/v2/mf_redemption_plans", body)


@router.post("/sandbox/stp", response_model=FpProxyResponse)
async def daily_stp(body: Any = Body(...)) -> FpProxyResponse:
    """Systematic transfer plan (``POST /v2/mf_switch_plans``)."""
    return await _fp_pass("order", "POST", "/v2/mf_switch_plans", body)


# ---- daily reads (analyse) -------------------------------------------------


@router.get("/sandbox/schemes", response_model=FpProxyResponse)
async def daily_schemes() -> FpProxyResponse:
    """The full transactable scheme universe on this gateway (ABSL + ICICI on
    sandbox), with fund/scheme names expanded."""
    return await _fp_pass(
        "order",
        "GET",
        "/v2/mf_scheme_plans/cybrillapoa",
        params={"expand": ["mf_scheme", "mf_fund"]},
    )


@router.get("/sandbox/settlement-detail", response_model=FpProxyResponse)
async def daily_settlement_detail(mf_purchase: str) -> FpProxyResponse:
    """RTA settlement detail for one purchase (``GET /v2/mf_settlement_details``)."""
    return await _fp_pass(
        "order", "GET", "/v2/mf_settlement_details", params={"mf_purchase": mf_purchase}
    )


_AMC_BY_ISIN_PREFIX = {
    "INF209": "Aditya Birla Sun Life MF",
    "INF084": "Aditya Birla Sun Life MF",
    "INF109": "ICICI Prudential MF",
}


def _amc_of(isin: str) -> str:
    return _AMC_BY_ISIN_PREFIX.get((isin or "")[:6], "Other AMC")


@router.get("/sandbox/transactions", response_model=FpProxyResponse)
async def daily_transactions(mf_investment_account: str | None = None) -> FpProxyResponse:
    """Unified transaction feed for an account (or the whole tenant): merges
    purchases, SIP plans, redemptions and switches into one list, newest
    first. (FP's own unified ``GET /v2/transactions`` is tenant-gated 404 —
    this composes the same view from the four list APIs that ARE live.)"""
    _sandbox_only()
    params = (
        {"mf_investment_account": mf_investment_account}
        if mf_investment_account
        else None
    )
    feed: list[dict] = []
    sources = [
        ("purchase", "/v2/mf_purchases"),
        ("sip_plan", "/v2/mf_purchase_plans"),
        ("redemption", "/v2/mf_redemptions"),
        ("switch", "/v2/mf_switches"),
    ]
    for kind_name, path in sources:
        body = await _fp_get("order", path, params)
        rows = (body or {}).get("data") or []
        for o in rows:
            if mf_investment_account and o.get("mf_investment_account") != mf_investment_account:
                continue
            feed.append(
                {
                    "type": kind_name,
                    "id": o.get("id"),
                    "old_id": o.get("old_id"),
                    "mf_investment_account": o.get("mf_investment_account"),
                    "scheme": o.get("scheme") or o.get("from_scheme"),
                    "to_scheme": o.get("to_scheme"),
                    "amount": o.get("amount"),
                    "state": o.get("state"),
                    "created_at": o.get("created_at"),
                    "scheduled_on": o.get("scheduled_on"),
                    "failure_code": o.get("failure_code"),
                    "allotted_units": o.get("allotted_units"),
                    "purchased_price": o.get("purchased_price"),
                    "folio_number": o.get("folio_number"),
                    "frequency": o.get("frequency"),
                    "installment_day": o.get("installment_day"),
                    "number_of_installments": o.get("number_of_installments"),
                }
            )
    feed.sort(key=lambda x: x.get("created_at") or "", reverse=True)
    return FpProxyResponse(
        status=200,
        body={"object": "list", "count": len(feed), "data": feed},
    )


@router.get("/sandbox/holdings", response_model=FpProxyResponse)
async def daily_holdings(mf_investment_account: str | None = None) -> FpProxyResponse:
    """Holdings for an account. Primary source: ``GET /v2/mf_folios`` (real
    folios, minted on settlement). Until something settles those are empty, so
    the response also carries ``derived_from_orders`` — per-scheme units and
    cost built from SETTLED purchases — plus in-flight amounts, clearly
    labelled. There is no live-NAV API on this gateway, so value = cost basis."""
    _sandbox_only()
    params = (
        {"mf_investment_account": mf_investment_account}
        if mf_investment_account
        else None
    )
    folios_body = await _fp_get("order", "/v2/mf_folios", params)
    folios = (folios_body or {}).get("data") or []

    purchases = ((await _fp_get("order", "/v2/mf_purchases", params)) or {}).get("data") or []
    if mf_investment_account:
        purchases = [
            o for o in purchases if o.get("mf_investment_account") == mf_investment_account
        ]
    by_scheme: dict[str, dict] = {}
    for o in purchases:
        isin = o.get("scheme") or "?"
        row = by_scheme.setdefault(
            isin,
            {
                "scheme": isin,
                "amc": _amc_of(isin),
                "units": 0.0,
                "invested_settled": 0.0,
                "in_flight_amount": 0.0,
                "folio_number": None,
            },
        )
        if o.get("state") == "successful":
            row["units"] += float(o.get("allotted_units") or 0)
            row["invested_settled"] += float(o.get("amount") or 0)
            row["folio_number"] = o.get("folio_number") or row["folio_number"]
        elif o.get("state") in ("under_review", "pending", "confirmed", "submitted"):
            row["in_flight_amount"] += float(o.get("amount") or 0)
    derived = [r for r in by_scheme.values() if r["units"] or r["in_flight_amount"]]
    derived.sort(key=lambda r: -(r["invested_settled"] + r["in_flight_amount"]))
    return FpProxyResponse(
        status=200,
        body={
            "folios": folios,
            "derived_from_orders": derived,
            "note": (
                "folios[] is FP's real holdings record (appears after first "
                "settlement); derived_from_orders is built from this tenant's "
                "purchase states as a pre-settlement view. Cost basis only — "
                "the gateway exposes no live-NAV/valuation API."
            ),
        },
    )


@router.get("/sandbox/investor-report", response_model=FpProxyResponse)
async def daily_investor_report(mf_investment_account: str) -> FpProxyResponse:
    """Portfolio analysis for one investment account: status breakdown,
    per-scheme and per-AMC allocation, SIP commitments, folios. Composed
    server-side from the live list APIs (FP's Reports module is tenant-gated
    404 on sandbox — flag to FP for production)."""
    _sandbox_only()
    params = {"mf_investment_account": mf_investment_account}
    purchases = ((await _fp_get("order", "/v2/mf_purchases", params)) or {}).get("data") or []
    purchases = [
        o for o in purchases if o.get("mf_investment_account") == mf_investment_account
    ]
    plans = ((await _fp_get("order", "/v2/mf_purchase_plans", params)) or {}).get("data") or []
    plans = [
        o for o in plans if o.get("mf_investment_account") == mf_investment_account
    ]
    folios = ((await _fp_get("order", "/v2/mf_folios", params)) or {}).get("data") or []

    active_states = {"under_review", "pending", "confirmed", "submitted"}
    totals = {
        "invested_settled": 0.0,
        "units_allotted": 0.0,
        "in_flight_amount": 0.0,
        "failed_amount": 0.0,
        "orders_successful": 0,
        "orders_active": 0,
        "orders_failed": 0,
    }
    per_scheme: dict[str, dict] = {}
    for o in purchases:
        isin = o.get("scheme") or "?"
        row = per_scheme.setdefault(
            isin,
            {
                "scheme": isin,
                "amc": _amc_of(isin),
                "invested_settled": 0.0,
                "in_flight_amount": 0.0,
                "units": 0.0,
                "orders": 0,
                "folio_number": None,
            },
        )
        row["orders"] += 1
        amt = float(o.get("amount") or 0)
        state = o.get("state")
        if state == "successful":
            totals["orders_successful"] += 1
            totals["invested_settled"] += amt
            totals["units_allotted"] += float(o.get("allotted_units") or 0)
            row["invested_settled"] += amt
            row["units"] += float(o.get("allotted_units") or 0)
            row["folio_number"] = o.get("folio_number") or row["folio_number"]
        elif state in active_states:
            totals["orders_active"] += 1
            totals["in_flight_amount"] += amt
            row["in_flight_amount"] += amt
        elif state == "failed":
            totals["orders_failed"] += 1
            totals["failed_amount"] += amt
    exposure_total = totals["invested_settled"] + totals["in_flight_amount"]
    schemes = list(per_scheme.values())
    for row in schemes:
        row_exp = row["invested_settled"] + row["in_flight_amount"]
        row["allocation_pct"] = (
            round(row_exp / exposure_total * 100, 1) if exposure_total else 0.0
        )
    schemes.sort(key=lambda r: -(r["invested_settled"] + r["in_flight_amount"]))
    per_amc: dict[str, float] = {}
    for row in schemes:
        per_amc[row["amc"]] = (
            per_amc.get(row["amc"], 0.0)
            + row["invested_settled"]
            + row["in_flight_amount"]
        )
    amc_rows = [
        {
            "amc": k,
            "exposure": v,
            "allocation_pct": round(v / exposure_total * 100, 1) if exposure_total else 0.0,
        }
        for k, v in sorted(per_amc.items(), key=lambda kv: -kv[1])
    ]
    active_sips = [p for p in plans if p.get("state") in ("created", "active")]
    report = {
        "mf_investment_account": mf_investment_account,
        "totals": totals,
        "exposure_total": exposure_total,
        "allocation_by_scheme": schemes,
        "allocation_by_amc": amc_rows,
        "sips": {
            "active_count": len(active_sips),
            "monthly_commitment": sum(float(p.get("amount") or 0) for p in active_sips),
            "plans": [
                {
                    "id": p.get("id"),
                    "scheme": p.get("scheme"),
                    "amount": p.get("amount"),
                    "state": p.get("state"),
                    "installment_day": p.get("installment_day"),
                    "number_of_installments": p.get("number_of_installments"),
                }
                for p in plans
            ],
        },
        "folios": folios,
        "note": (
            "Values are cost basis: this gateway has no NAV/valuation API and "
            "FP's Reports module (holdings/capital-gains/XIRR) is tenant-gated "
            "404 on our sandbox — ask FP to enable it for production-grade "
            "reporting; until then Prozpr's own mf_nav_history prices holdings."
        ),
    }
    return FpProxyResponse(status=200, body=report)
