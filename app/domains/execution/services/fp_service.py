"""FP order execution — setup + KYC + lumpsum + SIP. Every body here is live-verified.

Setup chain (all-at-CREATE; the sandbox gateway blocks every update verb):
  1. POST /v2/investor_profiles      — demographics + FATCA inline
  2. POST /v2/email_addresses        — {profile, email}
  3. POST /v2/phone_numbers          — {profile, isd, number}
  4. POST /v2/addresses              — {profile, line1, city, state, postal_code, country}
  5. POST /v2/bank_accounts          — {profile, account_number, ifsc_code, type, primary_account_holder_name}
  6. POST /v2/mf_investment_accounts — {primary_investor, folio_defaults:{<ids of 2-5>}}
Orders (scheme = ISIN; user_ip mandatory):
  POST /v2/mf_purchases       {mf_investment_account, scheme, amount, user_ip}
  POST /v2/mf_purchase_plans  {..., frequency:'monthly', installment_day,
                               number_of_installments, systematic:true}

KYC = the Pre-Verification service (``cybrillarta`` tenant): an identity-match
check that gates our order UI. Note the sandbox OMS has its own independent
KYC-on-record check at settlement time which no sandbox PAN can pass — orders
create fine (``under_review``/``created``) but never settle here.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import CurrentUser
from app.domains.execution.models import FpExecAccount, FpExecOrder
from app.domains.execution.schemas.fp_schemas import (
    FpKycRequest,
    FpKycResponse,
    FpKycSetupResponse,
    FpLumpsumRequest,
    FpSetupRequest,
    FpSipRequest,
)
from app.domains.execution.services.fp_client import (
    FpError,
    get_fp_client,
    get_fp_preverify_client,
)
from app.domains.identity.models.user import User
from app.domains.mutual_funds.models.mf_fund_metadata import MfFundMetadata

logger = logging.getLogger(__name__)

_SANDBOX_ADDRESS = {
    "line1": "12 MG Road",
    "city": "Bengaluru",
    "state": "Karnataka",
    "postal_code": "560001",
    "country": "in",
}
_SANDBOX_EMAIL = "sandbox.test@example.com"
_SANDBOX_MOBILE = "9876543210"
_SANDBOX_BANK_ACCOUNT = "1234567890"
_SANDBOX_BANK_IFSC = "HDFC0000001"

# KYC page poll: Pre-Verification is async (accepted -> completed); give it a
# few seconds server-side before handing polling back to the client.
_KYC_POLL_ATTEMPTS = 5
_KYC_POLL_DELAY_S = 1.5


def _fp_http(exc: FpError, prefix: str) -> HTTPException:
    """FP 4xx -> 422 with FP's field messages (user-fixable); else 502."""
    detail = prefix
    body = exc.body
    if isinstance(body, dict):
        err = body.get("error") or body
        if isinstance(err, dict):
            msgs = err.get("errors")
            if isinstance(msgs, list):
                joined = "; ".join(
                    str(m.get("field", "")) + ": " + str(m.get("message", ""))
                    for m in msgs
                    if isinstance(m, dict)
                )
                if joined:
                    detail = prefix + " — " + joined
            elif err.get("message"):
                detail = prefix + " — " + str(err["message"])
    elif exc.args:
        detail = prefix + ": " + str(exc.args[0])
    code = (
        status.HTTP_422_UNPROCESSABLE_ENTITY
        if exc.status_code and 400 <= exc.status_code < 500
        else status.HTTP_502_BAD_GATEWAY
    )
    return HTTPException(status_code=code, detail=detail)


def _summarize_kyc(pv: dict) -> FpKycResponse:
    def field(name: str, key: str = "status"):
        section = pv.get(name)
        if isinstance(section, dict):
            return section.get(key)
        return None

    pv_status = str(pv.get("status") or "accepted")
    return FpKycResponse(
        id=str(pv.get("id") or ""),
        status=pv_status,
        verified=(
            pv_status == "completed"
            and field("pan") == "verified"
            and field("name") == "verified"
            and field("date_of_birth") == "verified"
        ),
        pan_status=field("pan"),
        pan_code=field("pan", "code"),
        name_status=field("name"),
        dob_status=field("date_of_birth"),
        readiness_status=field("readiness"),
        readiness_code=field("readiness", "code"),
    )


async def run_kyc_check(payload: FpKycRequest) -> FpKycResponse:
    """KYC readiness via the Pre-Verification service (``cybrillarta`` tenant) —
    the pre-onboarding gate, same as production. Stateless: FP owns the record;
    poll with ``get_kyc_check``."""
    async with get_fp_preverify_client() as client:
        try:
            pv = await client.request(
                "POST",
                "/poa/pre_verifications",
                json={
                    "pan": {"value": payload.pan},
                    "name": {"value": payload.name},
                    "date_of_birth": {"value": payload.date_of_birth},
                },
            )
        except FpError as exc:
            raise _fp_http(exc, "KYC check failed") from exc
    return _summarize_kyc(pv or {})


async def get_kyc_check(pv_id: str) -> FpKycResponse:
    async with get_fp_preverify_client() as client:
        try:
            pv = await client.request("GET", "/poa/pre_verifications/" + pv_id)
        except FpError as exc:
            raise _fp_http(exc, "KYC status fetch failed") from exc
    return _summarize_kyc(pv or {})


async def get_account(db: AsyncSession, user_id: uuid.UUID) -> FpExecAccount | None:
    result = await db.execute(
        select(FpExecAccount)
        .where(FpExecAccount.user_id == user_id)
        .order_by(FpExecAccount.created_at.asc())
    )
    return result.scalars().first()


async def ensure_account_row(db: AsyncSession, user_id: uuid.UUID) -> FpExecAccount:
    """Get-or-create the local FP account shell. Called at signup so every user
    has an investment-profile record from day one (``kyc_status='pending'``);
    the FP-side ids are minted later, once the KYC page collects a PAN."""
    account = await get_account(db, user_id)
    if account is not None:
        return account
    account = FpExecAccount(user_id=user_id, kyc_status="pending")
    db.add(account)
    await db.commit()
    await db.refresh(account)
    return account


async def setup_account(
    db: AsyncSession, user: CurrentUser, payload: FpSetupRequest
) -> FpExecAccount:
    """Run the 6-call setup chain and persist the resulting FP ids. Returns the
    existing row if the user already set up (FP allows one investment account
    per investor/holding pattern)."""
    account = await ensure_account_row(db, user.id)
    if account.fp_investment_account_id:
        return account

    email = payload.email or user.email or _SANDBOX_EMAIL
    mobile = payload.mobile or _SANDBOX_MOBILE
    bank_account = payload.bank_account_number or _SANDBOX_BANK_ACCOUNT
    bank_ifsc = payload.bank_ifsc or _SANDBOX_BANK_IFSC
    pan_lower = payload.pan.strip().lower()

    async with get_fp_client() as client:
        try:
            investor = await client.request(
                "POST",
                "/v2/investor_profiles",
                json={
                    "type": "individual",
                    "pan": pan_lower,
                    "name": payload.name,
                    "date_of_birth": payload.date_of_birth,
                    "tax_status": "resident_individual",
                    "gender": "male",
                    "pep_details": "not_applicable",
                    "occupation": "business",
                    "source_of_wealth": "salary",
                    "income_slab": "above_10lakh_upto_25lakh",
                    "country_of_birth": "in",
                    "place_of_birth": _SANDBOX_ADDRESS["city"],
                    "nationality_country": "in",
                    "citizenship_countries": ["in"],
                    "first_tax_residency": {
                        "country": "in",
                        "taxid_number": pan_lower,
                        "taxid_type": "pan",
                    },
                },
            )
            investor_id = investor["id"]
            email_row = await client.request(
                "POST",
                "/v2/email_addresses",
                json={"profile": investor_id, "email": email},
            )
            phone_row = await client.request(
                "POST",
                "/v2/phone_numbers",
                json={"profile": investor_id, "isd": "91", "number": mobile},
            )
            address_row = await client.request(
                "POST",
                "/v2/addresses",
                json={"profile": investor_id, **_SANDBOX_ADDRESS},
            )
            bank_row = await client.request(
                "POST",
                "/v2/bank_accounts",
                json={
                    "profile": investor_id,
                    "account_number": bank_account,
                    "ifsc_code": bank_ifsc,
                    "type": "savings",
                    "primary_account_holder_name": payload.name,
                },
            )
            mfia = await client.request(
                "POST",
                "/v2/mf_investment_accounts",
                json={
                    "primary_investor": investor_id,
                    "folio_defaults": {
                        "communication_email_address": email_row["id"],
                        "communication_mobile_number": phone_row["id"],
                        "communication_address": address_row["id"],
                        "payout_bank_account": bank_row["id"],
                    },
                },
            )
        except FpError as exc:
            raise _fp_http(exc, "FP account setup failed") from exc

    account.fp_investor_id = investor_id
    account.fp_investment_account_id = mfia["id"]
    account.pan = payload.pan.strip().upper()
    account.holder_name = payload.name
    account.bank_account_masked = "****" + bank_account[-4:]
    account.raw = {"investor": investor, "mf_investment_account": mfia}
    await db.commit()
    await db.refresh(account)
    return account


async def run_kyc_and_setup(
    db: AsyncSession, user: CurrentUser, pan: str | None
) -> FpKycSetupResponse:
    """The KYC page's flow: PAN from the request; name / DOB / email / mobile
    from the user's identity on our backend. Runs Pre-Verification, persists
    the outcome on the account row, and — once KYC completes — runs the full
    FP setup chain so the user can transact. Idempotent: call again (PAN
    optional) to re-poll a submitted check."""
    user_row = (
        await db.execute(select(User).where(User.id == user.id))
    ).scalar_one_or_none()
    if user_row is None:
        raise HTTPException(status_code=404, detail="User not found.")

    account = await ensure_account_row(db, user.id)

    effective_pan = (pan or account.pan or user_row.pan or "").strip().upper()
    if not effective_pan:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="PAN is required to run KYC.",
        )
    if user_row.date_of_birth is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Date of birth is missing on your profile — add it first.",
        )
    name = " ".join(
        p for p in (user_row.first_name or "", user_row.last_name or "") if p
    ).strip()
    if not name:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Your name is missing on your profile — add it first.",
        )
    dob = user_row.date_of_birth.isoformat()

    # Persist the PAN on both the account row and the user identity.
    if account.pan != effective_pan:
        account.pan = effective_pan
        account.kyc_pv_id = None
        account.kyc_status = "pending"
    if not user_row.pan:
        user_row.pan = effective_pan

    # Already fully ready — nothing to do.
    if account.kyc_status == "completed" and account.fp_investment_account_id:
        await db.commit()
        await db.refresh(account)
        return FpKycSetupResponse(kyc=None, account=account, ready_to_transact=True)

    # Run (or re-poll) the Pre-Verification check.
    if account.kyc_pv_id:
        kyc = await get_kyc_check(account.kyc_pv_id)
    else:
        kyc = await run_kyc_check(
            FpKycRequest(name=name, pan=effective_pan, date_of_birth=dob)
        )
    attempts = 0
    while kyc.status == "accepted" and attempts < _KYC_POLL_ATTEMPTS:
        await asyncio.sleep(_KYC_POLL_DELAY_S)
        kyc = await get_kyc_check(kyc.id)
        attempts += 1

    account.kyc_pv_id = kyc.id or account.kyc_pv_id
    account.kyc_checked_at = datetime.now(timezone.utc)
    if kyc.status == "completed":
        account.kyc_status = "completed" if kyc.verified else "failed"
    elif kyc.status == "accepted":
        account.kyc_status = "submitted"
    else:
        account.kyc_status = "failed"
    await db.commit()
    await db.refresh(account)

    # KYC passed -> mint the FP-side investor profile + investment account.
    if account.kyc_status == "completed" and not account.fp_investment_account_id:
        account = await setup_account(
            db,
            user,
            FpSetupRequest(
                name=name,
                pan=effective_pan,
                date_of_birth=dob,
                email=user_row.email,
                mobile=user_row.mobile,
            ),
        )

    return FpKycSetupResponse(
        kyc=kyc,
        account=account,
        ready_to_transact=bool(
            account.kyc_status == "completed" and account.fp_investment_account_id
        ),
    )


async def _resolve_isin(
    db: AsyncSession, scheme_code: str
) -> tuple[str, str | None, str | None]:
    """AMFI scheme_code -> (isin, scheme_code, name) via mf_fund_metadata; a raw
    ISIN (INF...) passes through untouched."""
    code = scheme_code.strip()
    if code.upper().startswith("INF"):
        result = await db.execute(
            select(MfFundMetadata).where(MfFundMetadata.isin == code.upper())
        )
        meta = result.scalars().first()
        return (
            code.upper(),
            meta.scheme_code if meta else None,
            meta.scheme_name if meta else None,
        )
    result = await db.execute(
        select(MfFundMetadata).where(MfFundMetadata.scheme_code == code)
    )
    meta = result.scalars().first()
    if meta is None or not meta.isin:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No ISIN on record for scheme "
            + code
            + "; pass an ISIN (INF...) directly.",
        )
    return meta.isin, meta.scheme_code, meta.scheme_name


async def _require_account(db: AsyncSession, user_id: uuid.UUID) -> FpExecAccount:
    account = await get_account(db, user_id)
    if account is None or not account.fp_investment_account_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Complete KYC to enable transactions (POST /fp/kyc-setup).",
        )
    return account


async def _record_order(
    db: AsyncSession,
    user_id: uuid.UUID,
    kind: str,
    isin: str,
    scheme_code: str | None,
    scheme_name: str | None,
    amount: float,
    resp: dict,
    installment_day: int | None = None,
    number_of_installments: int | None = None,
) -> FpExecOrder:
    order = FpExecOrder(
        user_id=user_id,
        kind=kind,
        scheme_code=scheme_code,
        isin=isin,
        scheme_name=scheme_name,
        amount=amount,
        installment_day=installment_day,
        number_of_installments=number_of_installments,
        fp_id=str(resp.get("id") or ""),
        state=str(resp.get("state") or "created"),
        raw=resp,
    )
    db.add(order)
    await db.commit()
    await db.refresh(order)
    return order


async def place_lumpsum(
    db: AsyncSession, user: CurrentUser, payload: FpLumpsumRequest, user_ip: str
) -> FpExecOrder:
    account = await _require_account(db, user.id)
    isin, scheme_code, scheme_name = await _resolve_isin(db, payload.scheme_code)
    async with get_fp_client() as client:
        try:
            resp = await client.request(
                "POST",
                "/v2/mf_purchases",
                json={
                    "mf_investment_account": account.fp_investment_account_id,
                    "scheme": isin,
                    "amount": payload.amount,
                    "user_ip": user_ip,
                },
            )
        except FpError as exc:
            raise _fp_http(exc, "Lumpsum order failed") from exc
    return await _record_order(
        db,
        user.id,
        kind="LUMPSUM",
        isin=isin,
        scheme_code=scheme_code,
        scheme_name=scheme_name,
        amount=payload.amount,
        resp=resp or {},
    )


async def place_sip(
    db: AsyncSession, user: CurrentUser, payload: FpSipRequest, user_ip: str
) -> FpExecOrder:
    account = await _require_account(db, user.id)
    isin, scheme_code, scheme_name = await _resolve_isin(db, payload.scheme_code)
    async with get_fp_client() as client:
        try:
            resp = await client.request(
                "POST",
                "/v2/mf_purchase_plans",
                json={
                    "mf_investment_account": account.fp_investment_account_id,
                    "scheme": isin,
                    "amount": payload.amount,
                    "frequency": "monthly",
                    "installment_day": payload.installment_day,
                    "number_of_installments": payload.number_of_installments,
                    "systematic": True,
                    "user_ip": user_ip,
                },
            )
        except FpError as exc:
            raise _fp_http(exc, "SIP order failed") from exc
    return await _record_order(
        db,
        user.id,
        kind="SIP",
        isin=isin,
        scheme_code=scheme_code,
        scheme_name=scheme_name,
        amount=payload.amount,
        resp=resp or {},
        installment_day=payload.installment_day,
        number_of_installments=payload.number_of_installments,
    )


async def execute_sip_plan(
    db: AsyncSession, user: CurrentUser, user_ip: str
) -> tuple[list[FpExecOrder], list[str]]:
    """Place an FP SIP purchase plan for every fund of the user's latest
    ``sip_monthly`` additional-investment run. Funds whose FP call fails are
    reported in ``failed`` — the rest proceed."""
    from app.domains.additional_investment.models.additional_investment_run import (
        AdditionalInvestmentBuy,
        AdditionalInvestmentRun,
        Cadence,
    )

    await _require_account(db, user.id)
    result = await db.execute(
        select(AdditionalInvestmentRun)
        .where(
            AdditionalInvestmentRun.user_id == user.id,
            AdditionalInvestmentRun.cadence == Cadence.SIP_MONTHLY,
        )
        .order_by(AdditionalInvestmentRun.created_at.desc())
    )
    run = result.scalars().first()
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No SIP plan found — set one up on the SIP page first.",
        )
    buys = (
        (
            await db.execute(
                select(AdditionalInvestmentBuy)
                .where(AdditionalInvestmentBuy.run_id == run.id)
                .order_by(AdditionalInvestmentBuy.rank.asc())
            )
        )
        .scalars()
        .all()
    )
    if not buys:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Your SIP plan has no funds to place.",
        )

    orders: list[FpExecOrder] = []
    failed: list[str] = []
    for buy in buys:
        amount = float(buy.monthly_amount_inr or buy.amount_inr or 0)
        if amount <= 0:
            continue
        scheme = buy.isin or buy.scheme_code
        try:
            order = await place_sip(
                db,
                user,
                FpSipRequest(scheme_code=str(scheme), amount=amount),
                user_ip,
            )
            orders.append(order)
        except HTTPException as exc:
            logger.warning(
                "FP SIP plan buy failed for %s: %s", buy.recommended_fund, exc.detail
            )
            failed.append(str(buy.recommended_fund) + ": " + str(exc.detail))
    return orders, failed


async def execute_rebalance_buys(
    db: AsyncSession,
    user: CurrentUser,
    user_ip: str,
    amounts: dict[str, float] | None = None,
) -> tuple[list[FpExecOrder], list[str]]:
    """Place an FP lumpsum for every pending (or previously failed — retryable)
    BUY trade of the user's latest rebalancing run, and mark each trade
    executed (``broker_ref`` = FP order id). ``amounts`` carries per-trade
    overrides from the order-review UI (trade id -> INR; <=0 skips the trade).
    Trades whose FP call fails are marked ``failed`` — the rest proceed. If
    NOTHING could be placed, raises 422 so the client never sees a false
    success."""
    from app.domains.rebalancing.models.rebalancing_run import RebalancingRun
    from app.domains.rebalancing.models.rebalancing_trade import (
        RebalancingTrade,
        TradeAction,
        TradeExecutionStatus,
    )

    account = await _require_account(db, user.id)
    result = await db.execute(
        select(RebalancingRun)
        .where(RebalancingRun.user_id == user.id)
        .order_by(RebalancingRun.created_at.desc())
    )
    run = result.scalars().first()
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No rebalancing run found — generate one first.",
        )
    trades = (
        (
            await db.execute(
                select(RebalancingTrade).where(
                    RebalancingTrade.run_id == run.id,
                    RebalancingTrade.action == TradeAction.BUY,
                    RebalancingTrade.execution_status.in_(
                        [TradeExecutionStatus.pending, TradeExecutionStatus.failed]
                    ),
                )
            )
        )
        .scalars()
        .all()
    )
    if not trades:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No pending BUY trades in the latest run.",
        )

    orders: list[FpExecOrder] = []
    failed: list[str] = []
    async with get_fp_client() as client:
        for trade in trades:
            isin = (trade.isin or "").strip().upper()
            override = (amounts or {}).get(str(trade.id))
            amount = float(override) if override is not None else float(trade.amount_inr or 0)
            if amount <= 0:
                continue  # user zeroed this trade out on the review screen
            if not isin.startswith("INF"):
                failed.append(str(trade.recommended_fund) + ": no ISIN on record")
                continue
            try:
                resp = await client.request(
                    "POST",
                    "/v2/mf_purchases",
                    json={
                        "mf_investment_account": account.fp_investment_account_id,
                        "scheme": isin,
                        "amount": amount,
                        "user_ip": user_ip,
                    },
                )
            except FpError as exc:
                logger.warning(
                    "FP rebalance buy failed for %s: %s", trade.recommended_fund, exc
                )
                trade.execution_status = TradeExecutionStatus.failed
                reason = _fp_http(exc, "order rejected").detail
                failed.append(str(trade.recommended_fund) + ": " + str(reason))
                continue
            order = await _record_order(
                db,
                user.id,
                kind="LUMPSUM",
                isin=isin,
                scheme_code=None,
                scheme_name=trade.recommended_fund,
                amount=amount,
                resp=resp or {},
            )
            trade.execution_status = TradeExecutionStatus.executed
            trade.executed_at = datetime.now(timezone.utc)
            trade.broker_ref = order.fp_id
            orders.append(order)
    await db.commit()
    if not orders and failed:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No orders could be placed — the sandbox rejected every scheme: "
            + "; ".join(failed[:6]),
        )
    return orders, failed


async def list_orders(db: AsyncSession, user_id: uuid.UUID) -> list[FpExecOrder]:
    result = await db.execute(
        select(FpExecOrder)
        .where(FpExecOrder.user_id == user_id)
        .order_by(FpExecOrder.created_at.desc())
    )
    return list(result.scalars().all())


async def refresh_order(
    db: AsyncSession, user_id: uuid.UUID, order_id: uuid.UUID
) -> FpExecOrder:
    """Re-read the order/plan from FP and store its current state."""
    result = await db.execute(
        select(FpExecOrder).where(
            FpExecOrder.id == order_id, FpExecOrder.user_id == user_id
        )
    )
    order = result.scalars().first()
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found.")
    path = "/v2/mf_purchase_plans" if order.kind == "SIP" else "/v2/mf_purchases"
    async with get_fp_client() as client:
        try:
            resp = await client.request("GET", path + "/" + order.fp_id)
        except FpError as exc:
            raise _fp_http(exc, "Order status fetch failed") from exc
    if isinstance(resp, dict) and resp.get("state"):
        order.state = str(resp["state"])
        order.raw = resp
    await db.commit()
    await db.refresh(order)
    return order
