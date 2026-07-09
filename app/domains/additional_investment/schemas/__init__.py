"""Pydantic response schemas for the additional-investment read endpoints.

The Invest page reads the customer's latest MONTHLY SIP deployment plan via
``GET /additional-investment/sip``. The plan itself is generated inside chat —
the ``additional_investment`` intent persists an ``AdditionalInvestmentRun`` with
its BUY children (see ``additional_investment_persist_service``). These schemas
reshape that persisted run into the flat, per-fund monthly view the frontend
renders; they are the read/serve channel deferred in
``additional_investment_module_service`` (Task 5/6).

Money is plain ``float`` rupees (the allocation family this domain follows); the
read service casts the ORM ``Numeric(18, 2)`` Decimals to float before building
these models.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class SipCreateRequest(BaseModel):
    """Set-up request from the Invest page's "Start a SIP" action.

    Only the per-month amount is taken from the client; the engine derives the
    fund split from the customer's goals/allocation, so there is nothing else to
    supply. Re-submitting simply computes a fresh run — the latest is what the
    Invest page shows (BUY-only / write-once, no update-in-place).
    """

    monthly_amount_inr: float = Field(
        gt=0, description="Fresh money to invest each month, in rupees."
    )


class SipFundBuy(BaseModel):
    """One fund in the SIP plan with its per-month contribution."""

    recommended_fund: str
    sub_category: str
    asset_subgroup: str
    # AMFI scheme code — lets the Invest page deep-link each fund to its detail
    # page (`/discovery/mf/:schemeCode`).
    scheme_code: str
    monthly_amount_inr: float
    rank: int
    reason: str


class SipPlanResponse(BaseModel):
    """The customer's latest monthly-SIP deployment plan for the Invest page.

    ``has_plan`` is False (and the numeric fields stay 0 / ``buys`` empty) when
    the customer has never set up a SIP through chat — the frontend then shows
    its "start a SIP" prompt instead of a plan.
    """

    has_plan: bool
    run_id: Optional[uuid.UUID] = None
    created_at: Optional[datetime] = None
    # Total fresh money the plan deploys each month (the run's deploy amount).
    monthly_amount_inr: float = 0.0
    # Of the monthly amount: how much landed in funds vs. couldn't be placed
    # (per-fund caps / fund scarcity). ``deployed + undeployed == amount``.
    monthly_deployed_inr: float = 0.0
    monthly_undeployed_inr: float = 0.0
    # Horizon the SIP leans toward: "short_term" | "medium_term" | "long_term";
    # None when there is no plan. Engine context — the frontend narrates a
    # friendly label, it never surfaces this raw value.
    target_bucket: Optional[str] = None
    fund_count: int = 0
    buys: List[SipFundBuy] = []

    # ── The canonical monthly SIP ──
    # personal_finance_profiles.starting_monthly_investment — the SINGLE source of
    # truth for "the customer's monthly SIP", read by the goal planner, the goals
    # timeline and the IPS. None when the user has never set one. Creating a plan
    # writes it (see additional_investment_create_service), so it and
    # ``monthly_amount_inr`` agree right after a create. Surfaced here so the
    # Invest page can pre-fill its set-up form from a SIP the customer already
    # entered elsewhere (onboarding / goal planner) rather than start blank.
    goal_plan_monthly_investment_inr: Optional[float] = None
    # False only when this plan's fund split is STALE: the canonical SIP was
    # changed on another surface after this plan was computed, so the per-fund
    # amounts no longer add up to it. The frontend then offers to recompute the
    # plan at the canonical amount. True when there is no plan to compare.
    goal_plan_in_sync: bool = True


__all__ = ["SipCreateRequest", "SipFundBuy", "SipPlanResponse"]
