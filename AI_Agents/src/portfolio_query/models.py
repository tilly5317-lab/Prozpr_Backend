from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator


# ---------------------------------------------------------------------------
# Conversation + I/O
# ---------------------------------------------------------------------------


class ConversationTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str


_DEFAULT_REDIRECT = (
    "That's outside what I can help with here — I can answer questions about "
    "your own portfolio and the current market outlook."
)


class PortfolioQueryResponse(BaseModel):
    answer: Optional[str] = None
    guardrail_triggered: bool
    redirect_message: Optional[str] = None
    # The agent's opinion that this question belongs to a different specialist.
    # REPORTED, NEVER ACTED ON — the reply is unaffected. The app layer records
    # it so we can measure how often the router and the agents disagree before
    # deciding whether a routing handoff is worth building.
    suggested_intent: Optional[str] = None
    # Which of the skill's three paths the agent took: X (out of scope), M
    # (market question) or P (portfolio question). Reported for telemetry only —
    # the choice already shaped the reply; nothing downstream branches on it.
    path: Optional[str] = None

    @model_validator(mode="after")
    def _enforce_guardrail_contract(self) -> "PortfolioQueryResponse":
        """Deterministic backstop for the guardrail contract.

        The bridge renders ``answer or redirect_message``, so a populated
        ``answer`` always wins. If the guardrail fires we must guarantee no
        out-of-scope ``answer`` reaches the customer — even when the LLM forgets
        to null it. Clear ``answer`` and ensure a redirect is present.
        """
        if self.guardrail_triggered:
            self.answer = None
            if not (self.redirect_message and self.redirect_message.strip()):
                self.redirect_message = _DEFAULT_REDIRECT
        return self


# ---------------------------------------------------------------------------
# Client context
# ---------------------------------------------------------------------------


class ClientContext(BaseModel):
    age: int | None = None
    risk_category: str | None = None
    effective_risk_score: float | None = None
    investment_horizon: str | None = None
    occupation_type: str | None = None
    annual_income_inr: float | None = None
    total_liabilities_inr: float | None = None
    financial_goals: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Portfolio context
# ---------------------------------------------------------------------------


class Holding(BaseModel):
    name: str
    instrument_type: str | None = None
    asset_class: str | None = None
    sub_category: str | None = None
    quantity: float | None = None
    current_value_inr: float | None = None
    allocation_percentage: float | None = None
    return_1y_pct: float | None = None
    return_3y_pct: float | None = None
    # Cost-basis-derived returns. Populated when avg_cost × quantity is known
    # — independent of the (often-NULL) trailing-window return columns above.
    invested_amount_inr: float | None = None
    gain_inr: float | None = None
    gain_pct: float | None = None
    # Annualised, from this fund's own cashflows. None for non-MF holdings and
    # for funds with too little history to solve.
    xirr_pct: float | None = None


class AllocationRow(BaseModel):
    asset_class: str
    percentage: float
    amount_inr: float | None = None


class SubCategoryAllocationRow(BaseModel):
    asset_class: str | None = None
    sub_category: str
    percentage: float
    amount_inr: float | None = None


class PortfolioContext(BaseModel):
    total_value_inr: float | None = None
    total_invested_inr: float | None = None
    total_gain_percentage: float | None = None
    # Annualised return computed from MF transaction cash flows (Newton-Raphson XIRR).
    # Falls back to None when there are fewer than 2 dated cash flows or solver fails.
    xirr_pct: float | None = None
    holdings: list[Holding] = Field(default_factory=list)
    allocations: list[AllocationRow] = Field(default_factory=list)
    sub_category_allocations: list[SubCategoryAllocationRow] = Field(
        default_factory=list
    )
    # Itemization metadata: ``holdings`` lists only the largest holdings by
    # value (the app layer caps itemization to bound prompt size). Counts are
    # computed over the FULL portfolio before the cap, so count questions stay
    # exact; the omitted_* fields describe the non-itemized tail (None when
    # nothing was omitted).
    total_holdings_count: int | None = None
    holdings_count_by_type: dict[str, int] | None = None
    omitted_holdings_count: int | None = None
    omitted_holdings_value_inr: float | None = None
