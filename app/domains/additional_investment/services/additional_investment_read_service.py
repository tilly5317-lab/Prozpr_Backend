"""Read the customer's latest monthly-SIP deployment plan.

The additional-investment engine persists one ``AdditionalInvestmentRun`` per
chat deployment (see ``additional_investment_persist_service``). This read
surface serves the LATEST ``sip_monthly`` run for a user, reshaped into the flat
per-fund monthly view the Invest page renders — the counterpart to the
"read/serve" HTTP channel deferred in ``additional_investment_module_service``.

BUY-only / write-once: there is no status to filter on, so "latest" is simply
the most recent ``created_at``. Only ``sip_monthly`` runs are surfaced here — a
lumpsum deployment is not a SIP. Money is cast from the ORM ``Numeric(18, 2)``
Decimals to plain float (the allocation family) before it leaves this layer.
"""

from __future__ import annotations

import uuid
from typing import Any, Iterable, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domains.additional_investment.models import (
    AdditionalInvestmentBuy,
    AdditionalInvestmentRun,
    Cadence,
)
from app.domains.additional_investment.schemas import (
    AssetClassBreakdown,
    AssetClassBreakdownRow,
    LumpsumFundBuy,
    LumpsumPlanResponse,
    SipFundBuy,
    SipPlanResponse,
)
from app.domains.additional_investment.services.lumpsum_reasoning import (
    build_fund_reason,
    reason_by_subgroup,
)
from app.domains.profile.models.personal_finance_profile import (
    PersonalFinanceProfile,
)
from app.domains.profile.models.saved_investment_preference import (
    SavedInvestmentPreference,
)
from app.domains.profile.services.profile_finance import (
    starting_monthly_investment_pfp,
)


def _monthly_amount(buy: AdditionalInvestmentBuy) -> float:
    """Per-month contribution for a SIP buy.

    ``monthly_amount_inr`` is the canonical SIP figure the engine sets; fall back
    to ``amount_inr`` defensively (the two are equal for a ``sip_monthly`` buy).
    """
    amount: Any = (
        buy.monthly_amount_inr
        if buy.monthly_amount_inr is not None
        else buy.amount_inr
    )
    return float(amount)


_ASSET_CLASS_ORDER = ("Equity", "Debt", "Others")


def build_ainv_asset_class_breakdown(
    rows: Iterable[tuple[str | None, str | None, float]],
) -> Optional[AssetClassBreakdown]:
    """Look-through Equity / Debt / Commodity split of an AINV deployment.

    ``rows`` are ``(asset_subgroup, sub_category, amount)`` per fund bought. This
    REUSES the rebalancing rollup ``asset_class_mix_from_rows`` with
    ``multi_asset_sleeve=True`` — a deployment is a PLAN, so the ``multi_asset``
    sleeve is split by the engine's own composition (65/25/10) rather than by the
    picked funds, exactly as the rebalancing TARGET bar does; otherwise an
    equity-heavy hybrid landing in the sleeve would silently delete the plan's
    debt. Target-only (a deployment has no "current"), so ``current_inr`` is 0 on
    every row. Returns None when nothing was deployed.
    """
    # Lazy import: keeps this module free of any load-order coupling to the
    # rebalancing package (the rollup itself is a leaf helper over
    # scheme_classification).
    from app.domains.rebalancing.services.asset_class_breakdown import (
        asset_class_mix_from_rows,
    )

    mix = asset_class_mix_from_rows(rows, multi_asset_sleeve=True)
    breakdown_rows = [
        AssetClassBreakdownRow(
            asset_class=asset_class,
            current_inr=0.0,
            target_inr=round(mix.get(asset_class, 0.0), 2),
        )
        for asset_class in _ASSET_CLASS_ORDER
        if mix.get(asset_class, 0.0) > 0
    ]
    if not breakdown_rows:
        return None
    return AssetClassBreakdown(
        rows=breakdown_rows,
        current_total_inr=0.0,
        target_total_inr=round(sum(mix.values()), 2),
    )


async def get_session_current_ainv(
    db: AsyncSession, user_id: uuid.UUID, session_id: uuid.UUID
) -> tuple[str | None, uuid.UUID | None]:
    """This chat SESSION's most recent additional-investment run, as
    ``(cadence, save_preference_run_id)`` — the two datums the chat restores its
    pills from on reload (history carries no per-message pill data):

    * ``cadence`` ("sip_monthly" | "lumpsum") restores "View plan" for ANY
      deploy; None when the session produced no run.
    * ``save_preference_run_id`` restores the "Save preference" pill, set ONLY
      when that same latest run carries an unsaved what-if candidate preference
      (``activated_at`` NULL). A later ordinary deploy (no candidate) or a
      candidate the customer already saved leaves it None — nothing left to save.

    One query, one row: both facts describe the session's latest run, so a later
    ordinary deploy correctly supersedes an earlier what-if. Session-scoped on
    purpose — a user's globally-latest run may belong to another conversation,
    and a SIP turn must not restore a lump-sum popup.
    """
    row = (
        await db.execute(
            select(
                AdditionalInvestmentRun.id,
                AdditionalInvestmentRun.cadence,
                AdditionalInvestmentRun.saved_investment_preference_id,
                SavedInvestmentPreference.activated_at,
            )
            .outerjoin(
                SavedInvestmentPreference,
                SavedInvestmentPreference.id
                == AdditionalInvestmentRun.saved_investment_preference_id,
            )
            .where(
                AdditionalInvestmentRun.user_id == user_id,
                AdditionalInvestmentRun.chat_session_id == session_id,
            )
            .order_by(AdditionalInvestmentRun.created_at.desc())
            .limit(1)
        )
    ).first()
    if row is None:
        return None, None
    run_id, cadence, preference_id, activated_at = row
    save_run_id = run_id if (preference_id is not None and activated_at is None) else None
    return (cadence.value if cadence is not None else None), save_run_id


async def get_latest_sip_plan(
    db: AsyncSession, user_id: uuid.UUID
) -> SipPlanResponse:
    """Return the user's most recent monthly-SIP plan.

    Yields ``SipPlanResponse(has_plan=False)`` when the customer has no
    ``sip_monthly`` run yet, so the Invest page can render its set-up prompt
    without special-casing a 404.
    """
    # The canonical monthly SIP (SSOT on PFP), which creating a plan keeps in step.
    # Surfaced alongside the plan so the Invest page can pre-fill from a SIP set on
    # another surface, and can spot a split gone stale after an edit elsewhere.
    pfp = (
        await db.execute(
            select(PersonalFinanceProfile).where(
                PersonalFinanceProfile.user_id == user_id
            )
        )
    ).scalars().first()
    goal_sip = starting_monthly_investment_pfp(pfp)  # Optional[float]

    stmt = (
        select(AdditionalInvestmentRun)
        .where(
            AdditionalInvestmentRun.user_id == user_id,
            AdditionalInvestmentRun.cadence == Cadence.SIP_MONTHLY,
        )
        .order_by(AdditionalInvestmentRun.created_at.desc())
        .options(selectinload(AdditionalInvestmentRun.buys))
        .limit(1)
    )
    run = (await db.execute(stmt)).scalars().first()
    if run is None:
        # No SIP to compare against — never nudge.
        return SipPlanResponse(
            has_plan=False,
            goal_plan_monthly_investment_inr=goal_sip,
            goal_plan_in_sync=True,
        )

    # Biggest monthly contribution first — mirrors the chat brief's ordering so
    # the customer sees the same "where most of the money goes" story.
    buys = [
        SipFundBuy(
            recommended_fund=b.recommended_fund,
            sub_category=b.sub_category,
            asset_subgroup=b.asset_subgroup,
            scheme_code=b.scheme_code,
            monthly_amount_inr=_monthly_amount(b),
            rank=b.rank,
            reason=b.reason,
        )
        for b in sorted(run.buys, key=_monthly_amount, reverse=True)
    ]

    monthly_amount_inr = float(run.deploy_amount_inr)
    # In sync when the canonical SIP is set and this plan was computed for it (to
    # the rupee). Creating a plan writes both, so a mismatch means the canonical
    # amount moved afterwards on another surface — this plan's split is stale.
    goal_plan_in_sync = (
        goal_sip is not None and abs(float(goal_sip) - monthly_amount_inr) < 1.0
    )

    breakdown = build_ainv_asset_class_breakdown(
        (b.asset_subgroup, b.sub_category, _monthly_amount(b)) for b in run.buys
    )

    return SipPlanResponse(
        has_plan=True,
        run_id=run.id,
        created_at=run.created_at,
        monthly_amount_inr=monthly_amount_inr,
        monthly_deployed_inr=float(run.deployed_inr),
        monthly_undeployed_inr=float(run.undeployed_inr),
        target_bucket=(
            run.target_bucket.value if run.target_bucket is not None else None
        ),
        fund_count=len(buys),
        buys=buys,
        goal_plan_monthly_investment_inr=goal_sip,
        goal_plan_in_sync=goal_plan_in_sync,
        asset_class_breakdown=breakdown,
    )


def _lumpsum_amount(buy: AdditionalInvestmentBuy) -> float:
    """One-time amount for a lumpsum buy.

    The engine leaves ``monthly_amount_inr`` None on a lumpsum buy and sets
    ``amount_inr``; read that (defensively fall back to monthly, though it should
    be None here).
    """
    amount: Any = (
        buy.amount_inr
        if buy.amount_inr is not None
        else buy.monthly_amount_inr
    )
    return float(amount)


async def get_latest_lumpsum_plan(
    db: AsyncSession, user_id: uuid.UUID
) -> LumpsumPlanResponse:
    """Return the user's most recent one-time lump-sum plan, with reasoning.

    Yields ``LumpsumPlanResponse(has_plan=False)`` when the customer has no
    ``lumpsum`` run yet, so the Invest page renders its set-up prompt. The
    per-part alignment (ideal vs current vs gap) and the per-fund "why this fund"
    reasoning are rebuilt from the ``deficit_facts`` persisted on the run
    (``request_input['deficit_facts']`` — written by the engine adapter), so this
    read matches the create response exactly.
    """
    stmt = (
        select(AdditionalInvestmentRun)
        .where(
            AdditionalInvestmentRun.user_id == user_id,
            AdditionalInvestmentRun.cadence == Cadence.LUMPSUM,
        )
        .order_by(AdditionalInvestmentRun.created_at.desc())
        .options(selectinload(AdditionalInvestmentRun.buys))
        .limit(1)
    )
    run = (await db.execute(stmt)).scalars().first()
    if run is None:
        return LumpsumPlanResponse(has_plan=False)

    # Deficit facts persisted alongside the run drive both the alignment section
    # and each fund's reason. Absent on a legacy run (persisted before the facts
    # were stored) — reasoning then degrades gracefully to a rank/category line.
    request_input = run.request_input or {}
    deficit_facts = request_input.get("deficit_facts")
    facts_by_sg = reason_by_subgroup(deficit_facts)

    buys = [
        LumpsumFundBuy(
            recommended_fund=b.recommended_fund,
            sub_category=b.sub_category,
            asset_subgroup=b.asset_subgroup,
            scheme_code=b.scheme_code,
            amount_inr=_lumpsum_amount(b),
            rank=b.rank,
            reason=build_fund_reason(
                recommended_fund=b.recommended_fund,
                sub_category=b.sub_category,
                asset_subgroup=b.asset_subgroup,
                rank=b.rank,
                amount_inr=_lumpsum_amount(b),
                deficit_row=facts_by_sg.get(b.asset_subgroup),
            ),
        )
        for b in sorted(run.buys, key=_lumpsum_amount, reverse=True)
    ]

    deployed_inr = float(run.deployed_inr)
    undeployed_inr = float(run.undeployed_inr)
    target_bucket = (
        run.target_bucket.value if run.target_bucket is not None else None
    )

    breakdown = build_ainv_asset_class_breakdown(
        (b.asset_subgroup, b.sub_category, _lumpsum_amount(b)) for b in run.buys
    )

    return LumpsumPlanResponse(
        has_plan=True,
        run_id=run.id,
        created_at=run.created_at,
        amount_inr=float(run.deploy_amount_inr),
        deployed_inr=deployed_inr,
        undeployed_inr=undeployed_inr,
        target_bucket=target_bucket,
        fund_count=len(buys),
        buys=buys,
        asset_class_breakdown=breakdown,
    )


__all__ = ["get_latest_sip_plan", "get_latest_lumpsum_plan"]
