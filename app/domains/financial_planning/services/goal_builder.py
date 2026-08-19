"""Turning "I want a car" into a costed, financed, saved goal.

Deliberately SLOT-DRIVEN rather than step-driven. There is no fixed script and
no question number: each turn we look at what is still unknown, hand that list
to the answer formatter with the numbers we have so far, and let it write one
natural question. So a customer who says everything at once ("a Thar in 3
years, about 18 lakh, no loan") goes straight to the summary, and a customer
who says "a car" gets asked which one — from the same code path.

What is NOT dynamic, on purpose: every number. Inflation, the loan split and
the EMI are computed in ``goals.services.goal_math`` and handed to the
formatter as finished figures. The model chooses words; it never chooses
amounts.

This module owns the CONVERSATION about a goal. Reading the message belongs to
``planning_extractor`` and touching the ``goals`` table belongs to
``goal_ops`` — so the same builder now serves an edit ("make the car 20 lakh")
as well as a creation, by loading the existing goal's figures into the draft
before it starts asking.
"""

from __future__ import annotations

import logging
from typing import Any

from app.domains.ai_engine.common import format_inr_indian
from app.domains.financial_planning.models.chat_goal_draft import (
    STAGE_COLLECTING,
    STAGE_CONFIRMING,
)
from app.domains.financial_planning.services import goal_ops, planning_state as state
from app.domains.financial_planning.services.plan_context import (
    ProfileContext,
    affordability,
    resolve_years,
    timing_blocker,
)
from app.domains.goals.services.goal_math import (
    MAX_COST,
    MAX_INTEREST,
    MAX_YEARS,
    MIN_YEARS,
    project_goal,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# What the goal still needs
# ---------------------------------------------------------------------------

# Slot -> the thing to ask for, in plain words. The formatter turns whichever of
# these are outstanding into ONE natural question; it is never shown as a list.
_ASK_FOR: dict[str, str] = {
    "goal_name": "which one specifically they have in mind",
    "years": "when they want it — how many years away",
    "cost_pv": "roughly what it costs today",
    "financed": "whether they plan to take a loan for it or pay outright",
    "down_payment": "how much they would put down themselves",
    "interest_pct": "the interest rate they expect on the loan",
    "tenure_years": "how many years they would take the loan over",
}

_CORE_SLOTS = ("goal_name", "years", "cost_pv")

# When the customer says "a car", the extractor names the goal but returns no
# price and no estimate — that pairing IS the signal that the thing is too vague
# to cost. Asking "roughly what does it cost?" about "a car" is a bad question;
# asking which one is the right one, and it gets us both.
_ASK_WHICH_ONE = (
    "which one specifically they have in mind, and roughly what that costs today"
)


def _is_vague(slots: dict[str, Any]) -> bool:
    """A named-but-unpriceable goal: a name, no cost, and the model could not
    even estimate one."""
    return (
        bool(slots.get("goal_name"))
        and slots.get("cost_pv") is None
        and slots.get("cost_source") is None
    )


def ask_list(
    slots: dict[str, Any],
    missing: list[str],
    profile: ProfileContext | None = None,
) -> list[str]:
    """Plain-English descriptions of the outstanding unknowns, for the formatter."""
    prof = profile or ProfileContext()
    if _is_vague(slots) and "cost_pv" in missing:
        rest = [_ASK_FOR[m] for m in missing if m not in ("cost_pv", "goal_name")]
        out = [_ASK_WHICH_ONE, *rest]
    else:
        out = [_ASK_FOR[m] for m in missing if m in _ASK_FOR]
    # If the only thing between us and a date is their age, say THAT rather than
    # "how many years away" — they already told us the age.
    blocker = timing_blocker(slots, prof)
    if blocker and "years" in missing:
        out = [blocker if a == _ASK_FOR["years"] else a for a in out]
    return out


def missing_slots(
    slots: dict[str, Any], profile: ProfileContext | None = None
) -> list[str]:
    """What we still need, in the order it is natural to ask.

    ``years`` counts as known when it can be DERIVED — "marriage at 30" plus a
    date of birth on file is a complete answer, and asking "how many years
    away?" after that is asking the customer to do our arithmetic.

    Financing is only raised once the goal itself is pinned down; asking about
    interest rates before we know what they are buying reads like a form.
    """
    prof = profile or ProfileContext()
    resolved = dict(slots)
    derived = resolve_years(slots, prof)
    if derived is not None:
        resolved["years"] = derived
    slots = resolved
    missing = [k for k in _CORE_SLOTS if slots.get(k) is None]
    if missing:
        return missing

    if slots.get("financed") is None:
        return ["financed"]
    if not slots.get("financed"):
        return []

    # Only the down payment is genuinely theirs to decide. The rate and tenure
    # come from their own stored planning assumptions
    # (cashflow_input_assumptions), so asking for them is asking for something
    # we already hold — we apply the defaults and say that we did.
    out: list[str] = []
    if slots.get("down_payment") is None and slots.get("down_payment_pct") is None:
        out.append("down_payment")
    return out


def sanitize(slots: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Drop values outside the rails, so a mis-parse becomes a re-ask rather
    than a goal with a 400-year horizon."""
    rejected: list[str] = []
    clean = dict(slots)
    years = clean.get("years")
    if years is not None and not (MIN_YEARS <= float(years) <= MAX_YEARS):
        rejected.append("years")
        clean.pop("years")
    cost = clean.get("cost_pv")
    if cost is not None and not (0 < float(cost) <= MAX_COST):
        rejected.append("cost_pv")
        clean.pop("cost_pv")
        clean.pop("cost_source", None)
    rate = clean.get("interest_pct")
    if rate is not None and not (0 <= float(rate) <= MAX_INTEREST):
        rejected.append("interest_pct")
        clean.pop("interest_pct")
    return clean, rejected


# ---------------------------------------------------------------------------
# Facts for the formatter
# ---------------------------------------------------------------------------


def money(value: Any) -> str | None:
    if value is None:
        return None
    return format_inr_indian(float(value))


def known_facts(slots: dict[str, Any]) -> dict[str, Any]:
    """The goal so far, in the shape the formatter should read it back in."""
    facts: dict[str, Any] = {}
    if slots.get("goal_name"):
        facts["goal"] = slots["goal_name"]
    if slots.get("goal_type"):
        facts["category"] = slots["goal_type"]
    if slots.get("years") is not None:
        facts["years_away"] = slots["years"]
    # Keep the form they used, so the reply can say "at 30" rather than
    # translating it back into "in 6 years" at them.
    if slots.get("target_age") is not None:
        facts["they_want_it_by_age"] = slots["target_age"]
    if slots.get("target_year") is not None:
        facts["they_want_it_by_year"] = slots["target_year"]
    if slots.get("cost_pv") is not None:
        facts["cost_today"] = money(slots["cost_pv"])
        facts["cost_today_is_our_estimate"] = (
            slots.get("cost_source") == "assistant_estimate"
        )
    if slots.get("financed") is not None:
        facts["taking_a_loan"] = bool(slots["financed"])
    if slots.get("down_payment") is not None:
        facts["down_payment"] = money(slots["down_payment"])
    if slots.get("down_payment_pct") is not None:
        facts["down_payment_pct"] = slots["down_payment_pct"]
    if slots.get("interest_pct") is not None:
        facts["interest_pct"] = slots["interest_pct"]
    if slots.get("tenure_years") is not None:
        facts["loan_years"] = slots["tenure_years"]
    if slots.get("editing_goal_id"):
        facts["this_is_an_edit_to_a_goal_they_already_have"] = True
    return facts


def projection_facts(proj) -> dict[str, Any]:
    """The computed numbers, pre-formatted. The model copies these verbatim."""
    facts: dict[str, Any] = {
        "cost_today": money(proj.cost_pv),
        "inflation_pct_used": proj.inflation_pct,
        "years_away": round(proj.years, 1),
        "cost_at_that_time": money(proj.cost_fv),
        "target_date": proj.target_date.isoformat(),
        "you_need_to_save": money(proj.corpus_required),
    }
    if proj.financed:
        facts.update(
            {
                "down_payment": money(proj.down_payment),
                "loan_amount": money(proj.loan_amount),
                "interest_pct": proj.interest_pct,
                "loan_years": proj.tenure_years,
                "monthly_emi": money(proj.monthly_emi),
                "total_interest_over_loan": money(proj.total_interest),
                "note_what_the_plan_saves_for": (
                    "only the down payment — the rest is borrowed, so it is an "
                    "EMI commitment rather than a corpus to build"
                ),
            }
        )
    return facts


def projection_to_json(proj) -> dict[str, Any]:
    """Persisted alongside the draft so a 'yes' commits the numbers they saw."""
    return {
        "cost_pv": proj.cost_pv,
        "cost_fv": proj.cost_fv,
        "inflation_pct": proj.inflation_pct,
        "years": proj.years,
        "target_date": proj.target_date.isoformat(),
        "financed": proj.financed,
        "down_payment": proj.down_payment,
        "loan_amount": proj.loan_amount,
        "interest_pct": proj.interest_pct,
        "tenure_years": proj.tenure_years,
        "monthly_emi": proj.monthly_emi,
        "total_interest": proj.total_interest,
        "corpus_required": proj.corpus_required,
    }


def build_projection(slots: dict[str, Any], profile: ProfileContext | None = None):
    """Fill loan terms from their own assumptions when they did not state them."""
    prof = profile or ProfileContext()
    financed = bool(slots.get("financed"))
    return project_goal(
        cost_pv=float(slots["cost_pv"]),
        years=float(slots["years"]),
        inflation_pct=slots.get("inflation_pct"),
        goal_type=str(slots.get("goal_type") or "OTHER"),
        financed=financed,
        down_payment=slots.get("down_payment"),
        down_payment_pct=slots.get("down_payment_pct"),
        interest_pct=(
            slots.get("interest_pct")
            if slots.get("interest_pct") is not None
            else (prof.default_loan_interest_pct if financed else None)
        ),
        tenure_years=(
            slots.get("tenure_years")
            if slots.get("tenure_years") is not None
            else (prof.default_loan_tenure_years if financed else None)
        ),
    )


def fallback_ask(
    missing: list[str],
    slots: dict[str, Any] | None = None,
    profile: ProfileContext | None = None,
) -> str:
    wants = ask_list(slots or {}, missing, profile)
    if not wants:
        return "Tell me a little more about this goal."
    if len(wants) == 1:
        return f"Could you tell me {wants[0]}?"
    return "Could you tell me " + ", ".join(wants[:-1]) + f", and {wants[-1]}?"


def fallback_summary(proj) -> str:
    lines = [
        f"Here's how it looks: {money(proj.cost_pv)} today, "
        f"about {money(proj.cost_fv)} in {round(proj.years, 1)} years at "
        f"{proj.inflation_pct:g}% inflation."
    ]
    if proj.financed:
        lines.append(
            f"With {money(proj.down_payment)} down, you'd borrow "
            f"{money(proj.loan_amount)} at {proj.interest_pct:g}% over "
            f"{proj.tenure_years:g} years — about {money(proj.monthly_emi)} a month."
        )
        lines.append(f"So the plan needs to build {money(proj.corpus_required)}.")
    else:
        lines.append(f"So the plan needs to build {money(proj.cost_fv)}.")
    lines.append("Shall I add this to your goals?")
    return " ".join(lines)


# ---------------------------------------------------------------------------
# Seeding a draft from a goal that already exists
# ---------------------------------------------------------------------------


def slots_from_goal(goal) -> dict[str, Any]:
    """Load an existing goal's figures into draft slots so an edit re-costs the
    whole thing rather than asking for everything again.

    ``cost_pv`` comes back from the stored FUTURE value, deflated by the rate we
    stored with it — the reverse of what ``apply_projection`` wrote. Without
    this, editing "make it 7 years instead of 5" would silently treat the
    inflated cost as today's price and inflate it a second time.
    """
    from datetime import date

    slots: dict[str, Any] = {"editing_goal_id": str(goal.id)}
    if goal.display_name:
        slots["goal_name"] = goal.display_name
    target = goal.goal_date or goal.target_date
    if target:
        years = (target - date.today()).days / 365.25
        if years > 0:
            slots["years"] = round(years, 2)
    financed = bool(goal.is_downpayment_only)
    slots["financed"] = financed
    if goal.inflation_rate is not None and not financed:
        slots["inflation_pct"] = float(goal.inflation_rate)

    cost_fv = float(goal.target_pv or goal.goal_value_fv or 0) or None
    if cost_fv and slots.get("years"):
        rate = float(slots.get("inflation_pct") or 0.0)
        slots["cost_pv"] = round(cost_fv / ((1.0 + rate / 100.0) ** slots["years"]), 2)
        slots["cost_source"] = "existing_goal"
    if financed:
        if goal.upfront_amount is not None:
            slots["down_payment"] = float(goal.upfront_amount)
        if goal.mortgage_interest_annual is not None:
            slots["interest_pct"] = float(goal.mortgage_interest_annual)
        if goal.mortgage_tenure_years is not None:
            slots["tenure_years"] = float(goal.mortgage_tenure_years)
    return slots


# ---------------------------------------------------------------------------
# Advancing the draft
# ---------------------------------------------------------------------------


async def advance(
    db,
    draft,
    merged: dict[str, Any],
    profile: ProfileContext,
) -> tuple[str, dict[str, Any], Any]:
    """Move the draft on by one step and say what the reply should contain.

    Returns ``(stage, facts, projection_or_None)``. The caller owns the LLM
    call, so this stays a pure decision about state: what we know, what is
    missing, and whether we can show numbers yet.
    """
    merged, rejected = sanitize(merged)
    derived = resolve_years(merged, profile)
    if derived is not None:
        merged["years"] = derived
    await state.update_draft_slots(db, draft, merged)

    missing = missing_slots(merged, profile)
    if missing:
        await state.set_draft_stage(db, draft, STAGE_COLLECTING)
        assumptions: dict[str, Any] = {}
        if merged.get("cost_source") == "assistant_estimate":
            assumptions["price_we_assumed"] = (
                f"{money(merged['cost_pv'])} is our estimate of what a "
                f"{merged.get('goal_name', 'this')} costs today, not their figure"
            )
        if rejected:
            assumptions["could_not_read"] = rejected
        facts = {
            "stage": "collecting",
            "known": known_facts(merged),
            "on_file": profile.as_facts(),
            "never_ask_for": profile.known_keys(),
            "still_needed": ask_list(merged, missing, profile),
        }
        afford = affordability(merged, profile)
        if afford:
            facts["affordability"] = afford
        if assumptions:
            facts["assumptions"] = assumptions
        return "collecting", facts, None

    proj = build_projection(merged, profile)
    await state.set_draft_projection(db, draft, projection_to_json(proj))
    await state.set_draft_stage(db, draft, STAGE_CONFIRMING)

    assumptions = {}
    if merged.get("cost_source") == "assistant_estimate":
        assumptions["price_we_assumed"] = (
            f"{money(proj.cost_pv)} is our estimate of today's price, not their figure"
        )
    if merged.get("inflation_pct") is None:
        assumptions["inflation_we_assumed"] = (
            f"{proj.inflation_pct:g}% a year, our default for "
            f"{merged.get('goal_type', 'this kind of goal')}"
        )
    if proj.financed and merged.get("interest_pct") is None:
        assumptions["loan_terms_we_assumed"] = (
            f"{proj.interest_pct:g}% over {proj.tenure_years:g} years, taken "
            "from their own saved planning assumptions"
        )

    facts = {
        "stage": "confirming",
        "known": known_facts(merged),
        "on_file": profile.as_facts(),
        "numbers": projection_facts(proj),
    }
    afford = affordability(merged, profile)
    if afford:
        facts["affordability"] = afford
    if assumptions:
        facts["assumptions"] = assumptions
    return "confirming", facts, proj


# ---------------------------------------------------------------------------
# Commit
# ---------------------------------------------------------------------------


async def commit(ctx, draft) -> tuple[Any, dict[str, Any], list[Any]]:
    """Write the confirmed draft — as a new goal, or over the one being edited.

    The row is built from the projection the customer SAW, not from a fresh
    calculation: if anything drifted between the summary and the "yes", they
    would be agreeing to numbers we then quietly changed.
    """
    import uuid as _uuid

    slots: dict[str, Any] = dict(draft.slots or {})
    proj: dict[str, Any] = dict(draft.projection or {})
    if not proj:
        return None, {}, []

    editing_id = slots.get("editing_goal_id")
    existing = None
    if editing_id:
        goals = await goal_ops.active_goals(ctx.db, ctx.effective_user_id)
        existing = next(
            (g for g in goals if str(g.id) == str(editing_id)), None
        )
        _ = _uuid  # id already stringified on the draft

    if existing is not None:
        before = goal_ops.to_json(existing)
        goal_ops.apply_projection(existing, slots, proj)
        await ctx.db.flush()
        write = await goal_ops.record_goal_write(
            ctx.db,
            user_id=ctx.effective_user_id,
            session_id=ctx.session_id,
            goal=existing,
            previous=before,
            new=goal_ops.to_json(existing),
            verbatim=draft.origin_question,
        )
        goal = existing
    else:
        goal = goal_ops.build_goal(ctx.effective_user_id, slots, proj)
        ctx.db.add(goal)
        await ctx.db.flush()
        write = await goal_ops.record_goal_write(
            ctx.db,
            user_id=ctx.effective_user_id,
            session_id=ctx.session_id,
            goal=goal,
            previous=None,
            new=goal_ops.to_json(goal),
            verbatim=draft.origin_question,
        )

    await state.mark_draft_committed(ctx.db, draft, goal.id)

    # A Retirement goal's target year IS the planned retirement age (SSOT), and
    # POST /goals mirrors it onto investment_profiles. Chat must not be the one
    # writer that skips it.
    try:
        from app.domains.goals.services.retirement_sync import (
            sync_retirement_age_from_goal,
        )

        await sync_retirement_age_from_goal(ctx.db, ctx.effective_user_id, goal)
    except Exception:
        logger.exception("retirement-age sync after a goal commit failed")

    logger.info(
        "financial_planning %s goal=%s (%s) for user=%s",
        "updated" if existing is not None else "created",
        goal.id,
        goal.display_name,
        ctx.effective_user_id,
    )
    return goal, proj, [write]


__all__ = [
    "advance",
    "ask_list",
    "build_projection",
    "commit",
    "fallback_ask",
    "fallback_summary",
    "known_facts",
    "missing_slots",
    "money",
    "projection_facts",
    "projection_to_json",
    "sanitize",
    "slots_from_goal",
]
