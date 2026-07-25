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
from typing import List, Literal, Optional

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


# ── Lump sum (one-time deployment) ─────────────────────────────────────────
# The Invest → Lump sum page (`/invest/lumpsum`) reads the customer's latest
# ONE-TIME deployment plan via `GET /additional-investment/lumpsum` and sets one
# up via `POST`. It runs the SAME additional-investment engine as the SIP path,
# with `cadence=lumpsum` — which triggers holdings-aware DEFICIT FILL: the money
# is steered into the parts of the portfolio furthest below their goal-based
# ideal. Those ideal/current/gap facts drive the per-fund reasoning and the
# "why these funds" alignment section, so the lumpsum shape carries a bit more
# than the SIP shape.


class LumpsumCreateRequest(BaseModel):
    """Set-up request from the Invest → Lump sum page's "Plan lump sum" action.

    Only the one-time amount is taken from the client; the engine derives the
    fund split from the customer's goals, ideal allocation and current holdings.
    ``action`` mirrors the page's Add / Withdraw toggle — only ``add`` is
    supported today (the additional-investment engine is BUY-only; ``withdraw``
    is rejected with a customer-facing message).
    """

    amount_inr: float = Field(
        gt=0, description="Fresh money to invest one-time, in rupees."
    )
    action: Literal["add", "withdraw"] = Field(
        default="add",
        description="'add' deploys fresh money; 'withdraw' is not yet supported.",
    )


class LumpsumAlignmentRow(BaseModel):
    """One part of the portfolio the lump sum was measured against.

    The "why these funds" section renders these: for each subgroup the money
    went into, how the customer's CURRENT holdings compare with their goal-based
    IDEAL, the resulting gap, and how much of this lump sum fills it. Subgroup is
    the raw engine id (for keys); ``label`` is the customer-facing name.
    """

    subgroup: str
    label: str
    asset_class: str
    ideal_inr: float
    current_inr: float
    gap_inr: float
    deploy_inr: float


class LumpsumFundBuy(BaseModel):
    """One fund in the lump-sum plan with its one-time amount and reasoning."""

    recommended_fund: str
    sub_category: str
    asset_subgroup: str
    # AMFI scheme code — deep-links the fund to `/discovery/mf/:schemeCode`.
    scheme_code: str
    amount_inr: float
    rank: int
    # Plain-English "why this fund" line: ties the buy to the customer's
    # goal-based ideal and how their current holdings compare (see
    # ``lumpsum_reasoning.build_fund_reason``).
    reason: str


class LumpsumPlanResponse(BaseModel):
    """The customer's latest one-time lump-sum deployment plan for the Invest page.

    ``has_plan`` is False (numeric fields 0, lists empty) when the customer has
    never planned a lump sum — the frontend then shows its set-up prompt.
    """

    has_plan: bool
    run_id: Optional[uuid.UUID] = None
    created_at: Optional[datetime] = None
    # Total fresh money the plan deploys (the run's deploy amount).
    amount_inr: float = 0.0
    # Of the amount: how much landed in funds vs. couldn't be placed (per-fund
    # caps / fund scarcity). ``deployed + undeployed == amount``.
    deployed_inr: float = 0.0
    undeployed_inr: float = 0.0
    # Horizon the deploy leaned toward: "short_term" | "medium_term" |
    # "long_term"; None when there is no plan. Engine context — the frontend
    # narrates a friendly label, never the raw value.
    target_bucket: Optional[str] = None
    fund_count: int = 0
    buys: List[LumpsumFundBuy] = []
    # Per-part current-vs-ideal alignment behind the plan (may be empty for a
    # legacy run persisted before the facts were stored).
    alignment_rows: List[LumpsumAlignmentRow] = []
    # One-line summary of the whole deployment in goal terms.
    headline_reason: Optional[str] = None


__all__ = [
    "SipCreateRequest",
    "SipFundBuy",
    "SipPlanResponse",
    "LumpsumCreateRequest",
    "LumpsumAlignmentRow",
    "LumpsumFundBuy",
    "LumpsumPlanResponse",
]
