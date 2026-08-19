"""What we already know about the customer, before we ask them anything.

The goal builder used to run on the draft alone, so it cheerfully asked for
things sitting in the database: "how old are you right now?" when the date of
birth is on the user record, and "how much are you investing each month?" when
the SIP is on the profile. Asking a customer for something you already hold
reads as not paying attention, and it is the fastest way to make a
conversational surface feel worse than the form it replaced.

Read from the ORM graph the turn already loaded, so this costs no extra query.
Everything here is a READ. Nothing in this module writes, and nothing here is
ever handed to the extractor — see ``privacy``; only the reply formatter sees
these values, and only the ones the reply is about.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Any

logger = logging.getLogger(__name__)


def age_from_dob(dob: date, as_of: date | None = None) -> int:
    """Whole years, birthday-aware."""
    today = as_of or date.today()
    return (
        today.year
        - dob.year
        - ((today.month, today.day) < (dob.month, dob.day))
    )


@dataclass(frozen=True)
class ProfileContext:
    """The subset of the customer's record a goal conversation can use."""

    first_name: str | None = None
    age: int | None = None
    date_of_birth: date | None = None
    annual_income: float | None = None
    monthly_expense: float | None = None
    monthly_sip: float | None = None
    financial_assets: float | None = None
    equity_shares: float | None = None
    existing_goal_names: tuple[str, ...] = ()
    # The customer's OWN planning assumptions (cashflow_input_assumptions),
    # stored as fractions. Using these rather than inventing rates means the
    # conversation never has to ask for a loan rate or tenure we already hold,
    # and the projection matches what the goal-planning screen would show.
    roi_near_pct: float = 5.0
    roi_mid_pct: float = 7.0
    roi_long_pct: float = 9.0
    near_horizon_years: int = 2
    mid_horizon_years: int = 3
    default_loan_interest_pct: float = 7.5
    default_loan_tenure_years: int = 20
    default_downpayment_pct: float = 20.0

    @property
    def investable_now(self) -> float:
        """What they already have working towards a goal."""
        return (self.financial_assets or 0.0) + (self.equity_shares or 0.0)

    def expected_return_pct(self, years: float) -> float:
        """Their post-tax return assumption for a goal this far out."""
        if years <= self.near_horizon_years:
            return self.roi_near_pct
        if years <= self.mid_horizon_years:
            return self.roi_mid_pct
        return self.roi_long_pct

    def known_keys(self) -> list[str]:
        """Names of the things we hold, for the "never ask for these" rule."""
        out = []
        if self.age is not None:
            out.append("their age / date of birth")
        if self.annual_income is not None:
            out.append("their income")
        if self.monthly_expense is not None:
            out.append("their monthly expenses")
        if self.monthly_sip is not None:
            out.append("their current monthly SIP")
        if self.financial_assets is not None:
            out.append("what they have saved")
        return out

    def as_facts(self) -> dict[str, Any]:
        """Shown to the answer formatter so it can use these instead of asking."""
        from app.domains.ai_engine.common import format_inr_indian

        facts: dict[str, Any] = {}
        if self.first_name:
            facts["first_name"] = self.first_name
        if self.age is not None:
            facts["their_age_now"] = self.age
        if self.annual_income is not None:
            facts["annual_income"] = format_inr_indian(self.annual_income)
        if self.monthly_expense is not None:
            facts["monthly_expenses"] = format_inr_indian(self.monthly_expense)
        if self.monthly_sip is not None:
            facts["monthly_sip_now"] = format_inr_indian(self.monthly_sip)
        if self.financial_assets is not None:
            facts["saved_so_far"] = format_inr_indian(self.financial_assets)
        if self.equity_shares:
            facts["equities_held"] = format_inr_indian(self.equity_shares)
        if self.existing_goal_names:
            facts["goals_already_in_their_plan"] = list(self.existing_goal_names)
        facts["their_loan_defaults"] = {
            "interest_pct": self.default_loan_interest_pct,
            "tenure_years": self.default_loan_tenure_years,
        }
        return facts


def _f(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def load_profile_context(user: Any) -> ProfileContext:
    """Read the preloaded user graph. Never raises — an empty context just
    means we ask for more, which is the pre-existing behaviour."""
    if user is None:
        return ProfileContext()
    try:
        dob = getattr(user, "date_of_birth", None)
        pfp = getattr(user, "personal_finance_profile", None)
        goals = getattr(user, "financial_goals", None) or []
        names = tuple(
            str(getattr(g, "name", None) or getattr(g, "goal_name", "") or "")
            for g in goals
            if str(getattr(g, "status", "")).upper().endswith("ACTIVE")
            or getattr(g, "status", None) is None
        )
        a = getattr(user, "cashflow_assumptions", None)

        def _pct(attr: str, fallback: float) -> float:
            """Assumptions are stored as fractions; the conversation speaks percent."""
            raw = _f(getattr(a, attr, None)) if a is not None else None
            return raw * 100.0 if raw is not None else fallback

        def _int(attr: str, fallback: int) -> int:
            raw = getattr(a, attr, None) if a is not None else None
            try:
                return int(raw) if raw is not None else fallback
            except (TypeError, ValueError):
                return fallback

        return ProfileContext(
            first_name=(getattr(user, "first_name", None) or "").strip() or None,
            age=age_from_dob(dob) if dob else None,
            date_of_birth=dob,
            annual_income=_f(getattr(pfp, "annual_income", None)),
            monthly_expense=_f(getattr(pfp, "monthly_household_expense", None)),
            monthly_sip=_f(getattr(pfp, "starting_monthly_investment", None)),
            financial_assets=_f(getattr(pfp, "financial_assets", None)),
            equity_shares=_f(getattr(pfp, "equity_shares", None)),
            existing_goal_names=tuple(n for n in names if n),
            roi_near_pct=_pct("roi_near_term_post_tax", 5.0),
            roi_mid_pct=_pct("roi_mid_term_post_tax", 7.0),
            roi_long_pct=_pct("roi_long_term_post_tax", 9.0),
            near_horizon_years=_int("near_term_horizon_years", 2),
            mid_horizon_years=_int("medium_term_horizon_years", 3),
            default_loan_interest_pct=_pct("default_mortgage_interest_annual", 7.5),
            default_loan_tenure_years=_int("default_mortgage_tenure_years", 20),
            default_downpayment_pct=_pct("default_property_downpayment_pct", 20.0),
        )
    except Exception:
        logger.exception("could not read profile context; continuing without it")
        return ProfileContext()


def resolve_years(slots: dict[str, Any], profile: ProfileContext) -> float | None:
    """Turn however the customer expressed the timing into a number of years.

    People say "in 5 years", "by 2032" and "at 30" interchangeably, and the last
    one is only answerable if we know how old they are — which we usually do.
    Resolving it here means the conversation never has to ask for an age we
    already hold.
    """
    years = slots.get("years")
    if years is not None:
        return float(years)

    target_year = slots.get("target_year")
    if target_year:
        delta = int(target_year) - date.today().year
        if delta > 0:
            return float(delta)

    target_age = slots.get("target_age")
    if target_age is not None:
        current = slots.get("current_age")
        if current is None:
            current = profile.age
        if current is not None and float(target_age) > float(current):
            return float(target_age) - float(current)

    return None


def timing_blocker(slots: dict[str, Any], profile: ProfileContext) -> str | None:
    """What is stopping us resolving the timing, in plain words for the ask.

    ``None`` means the timing is either resolved or simply not stated yet.
    """
    if resolve_years(slots, profile) is not None:
        return None
    if slots.get("target_age") is not None and profile.age is None:
        return (
            "how old they are now — they gave the age they want it BY, and we "
            "have no date of birth on record to work back from"
        )
    return None


def affordability(
    slots: dict[str, Any], profile: ProfileContext
) -> dict[str, Any] | None:
    """Can their current plan already pay for this goal outright?

    Answered BEFORE the loan question is asked, so the question can be the right
    one: "you'll already have enough — do you still want to finance it?" reads
    completely differently from "will you take a loan?", and only one of them
    respects what the customer has already told us about their money.

    ``None`` when we cannot say — no timing, no cost, or nothing invested to
    project. Saying nothing is better than implying a verdict we do not have.
    """
    from app.domains.ai_engine.common import format_inr_indian
    from app.domains.goals.services.goal_math import (
        DEFAULT_INFLATION,
        DEFAULT_INFLATION_BY_TYPE,
        future_value,
        project_corpus,
    )

    years = resolve_years(slots, profile)
    cost_pv = slots.get("cost_pv")
    if years is None or cost_pv is None:
        return None
    if profile.investable_now <= 0 and not profile.monthly_sip:
        return None

    infl = slots.get("inflation_pct")
    if infl is None:
        infl = DEFAULT_INFLATION_BY_TYPE.get(
            str(slots.get("goal_type") or "OTHER").upper(), DEFAULT_INFLATION
        )
    cost_then = future_value(float(cost_pv), float(infl), years)
    roi = profile.expected_return_pct(years)
    corpus_then = project_corpus(
        current_assets=profile.investable_now,
        monthly_sip=profile.monthly_sip or 0.0,
        years=years,
        annual_return_pct=roi,
    )
    covers = corpus_then >= cost_then
    return {
        "projected_savings_by_then": format_inr_indian(corpus_then),
        "goal_will_cost_then": format_inr_indian(cost_then),
        "current_plan_covers_it": covers,
        "surplus_if_covered": format_inr_indian(corpus_then - cost_then)
        if covers
        else None,
        "shortfall_if_not": format_inr_indian(cost_then - corpus_then)
        if not covers
        else None,
        "assumed_return_pct": roi,
        "based_on": (
            "their existing monthly SIP and what they already have invested — "
            "this goal alone, not counting their other goals"
        ),
    }


__all__ = [
    "ProfileContext",
    "affordability",
    "age_from_dob",
    "load_profile_context",
    "resolve_years",
    "timing_blocker",
]
