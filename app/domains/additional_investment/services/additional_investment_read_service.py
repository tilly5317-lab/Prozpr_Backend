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
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domains.additional_investment.models import (
    AdditionalInvestmentBuy,
    AdditionalInvestmentRun,
    Cadence,
)
from app.domains.additional_investment.schemas import SipFundBuy, SipPlanResponse
from app.domains.profile.models.personal_finance_profile import (
    PersonalFinanceProfile,
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
    )


__all__ = ["get_latest_sip_plan"]
