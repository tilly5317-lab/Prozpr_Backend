"""Deterministic lever generators (A through G).

Each generator returns a Lever | None. None means the lever doesn't apply or can't close
the gap within its search bounds. Per spec §8.4 every lever asserts mid-horizon corpus
non-negativity.
"""
from __future__ import annotations
from datetime import date

from cashflow_statement.models import (
    GoalPlanningInput, GoalPlanningOutput, Lever, NumericOverride,
    GoalMutation,
)
from cashflow_statement.engine import compute_full_projection


def _is_feasible(out: GoalPlanningOutput) -> bool:
    """Canonical end-of-horizon feasibility — thin wrapper over HeadlineStatus.is_feasible.

    Kept as a function (rather than inlining `out.headline.is_feasible` at every
    call site) so the lever engine has a single read-point if the feasibility
    rule ever needs to diverge for lever-specific logic.
    """
    return out.headline.is_feasible


def _apply_goal_mutation(inp: GoalPlanningInput, goal_name: str, fields: dict) -> GoalPlanningInput:
    """Helper: produce a new input with a goal mutation applied. Treats retirement as a goal."""
    new_inp = inp.model_copy(deep=True)
    if goal_name == "retirement":
        new_inp.retirement = inp.retirement.model_copy(update=fields)
    else:
        for i, g in enumerate(new_inp.custom_goals):
            if g.name.casefold() == goal_name.casefold():
                new_inp.custom_goals[i] = g.model_copy(update=fields)
                return new_inp
        for i, g in enumerate(new_inp.goal_properties):
            if g.name.casefold() == goal_name.casefold():
                new_inp.goal_properties[i] = g.model_copy(update=fields)
                return new_inp
    return new_inp


def generate_lever_a_increase_sip(
    inp: GoalPlanningInput, baseline_out: GoalPlanningOutput,
    sip_max_multiplier: float = 5.0,
) -> Lever | None:
    """Lever A: bisect monthly_investment from current to N× to find smallest feasible value."""
    if _is_feasible(baseline_out):
        return None

    base_sip = inp.profile.starting_monthly_investment or 1
    lo, hi = base_sip, base_sip * sip_max_multiplier
    best: tuple[float, GoalPlanningOutput] | None = None

    for _ in range(8):
        mid = (lo + hi) / 2
        new_inp = inp.model_copy(deep=True)
        new_inp.profile = inp.profile.model_copy(update={"starting_monthly_investment": mid})
        new_out = compute_full_projection(new_inp)
        if _is_feasible(new_out):
            best = (mid, new_out)
            hi = mid
        else:
            lo = mid

    if best is None:
        return None
    target_sip, target_out = best
    confidence = "high" if target_sip < 2 * base_sip else ("medium" if target_sip < 3 * base_sip else "low")
    return Lever(
        description=f"Increase monthly investment from ₹{base_sip:,.0f} to ₹{target_sip:,.0f}",
        action=NumericOverride(
            kind="numeric", key="starting_monthly_investment", value=target_sip,
        ),
        projected_outcome=target_out.headline,
        confidence=confidence,
    )


def _add_years(d: date, n: int) -> date:
    try:
        return d.replace(year=d.year + n)
    except ValueError:
        return d.replace(year=d.year + n, day=28)


def generate_lever_b_defer_goal(
    inp: GoalPlanningInput, baseline_out: GoalPlanningOutput, defer_max_years: int = 10,
) -> Lever | None:
    """Lever B: defer largest underfunded goal 1-N years; smallest deferral that closes gap."""
    if _is_feasible(baseline_out):
        return None
    underfunded = [g for g in baseline_out.goals if g.shortfall_fv > 0]
    if not underfunded:
        return None
    largest = max(underfunded, key=lambda g: g.shortfall_fv)

    for years in range(1, defer_max_years + 1):
        new_date = _add_years(largest.goal_date, years)
        new_inp = _apply_goal_mutation(inp, largest.name, {"goal_date": new_date})
        new_out = compute_full_projection(new_inp)
        if _is_feasible(new_out):
            confidence = "high" if years <= 2 else ("medium" if years <= 5 else "low")
            return Lever(
                description=f"Defer '{largest.name}' by {years} years",
                action=GoalMutation(
                    kind="mutation", op="update", goal_name=largest.name,
                    fields={"goal_date": new_date},
                ),
                projected_outcome=new_out.headline,
                confidence=confidence,
            )
    return None


def generate_lever_c_reduce_target(
    inp: GoalPlanningInput, baseline_out: GoalPlanningOutput, reduce_max_pct: float = 0.50,
) -> Lever | None:
    """Lever C: reduce largest underfunded goal target in 5pp steps to 50%."""
    if _is_feasible(baseline_out):
        return None
    underfunded = [g for g in baseline_out.goals if g.shortfall_fv > 0]
    if not underfunded:
        return None
    largest = max(underfunded, key=lambda g: g.shortfall_fv)

    for pct in range(5, int(reduce_max_pct * 100) + 1, 5):
        cut = pct / 100
        new_goal_value_pv = largest.goal_value_pv * (1 - cut)
        new_inp = _apply_goal_mutation(inp, largest.name, {"goal_value_pv": new_goal_value_pv})
        new_out = compute_full_projection(new_inp)
        if _is_feasible(new_out):
            confidence = "high" if pct <= 15 else ("medium" if pct <= 30 else "low")
            return Lever(
                description=f"Reduce '{largest.name}' target by {pct}%",
                action=GoalMutation(
                    kind="mutation", op="update", goal_name=largest.name,
                    fields={"goal_value_pv": new_goal_value_pv},
                ),
                projected_outcome=new_out.headline,
                confidence=confidence,
            )
    return None


def generate_lever_d_retirement_age(
    inp: GoalPlanningInput, baseline_out: GoalPlanningOutput,
) -> Lever | None:
    """Lever D: bisect retirement_age upward. Only when retirement is underfunded."""
    retirement_status = next(
        (g for g in baseline_out.goals if g.goal_type.value == "retirement"), None
    )
    if retirement_status is None or retirement_status.shortfall_fv == 0:
        return None
    if _is_feasible(baseline_out):
        return None

    base_age = inp.retirement.retirement_age
    upper = inp.retirement.assumed_lifespan_years - 5

    for new_age in range(base_age + 1, upper + 1):
        new_inp = _apply_goal_mutation(inp, "retirement", {"retirement_age": new_age})
        new_out = compute_full_projection(new_inp)
        if _is_feasible(new_out):
            return Lever(
                description=f"Retire at {new_age} instead of {base_age}",
                action=GoalMutation(
                    kind="mutation", op="update", goal_name="retirement",
                    fields={"retirement_age": new_age},
                ),
                projected_outcome=new_out.headline,
                confidence="medium",
            )
    return None


def generate_lever_e_step_up(
    inp: GoalPlanningInput, baseline_out: GoalPlanningOutput, step_up_max_delta_pp: float = 0.20,
) -> Lever | None:
    """Lever E: bisect annual_invested_amount_growth from baseline to baseline+max_delta."""
    if _is_feasible(baseline_out):
        return None
    base_rate = inp.assumptions.annual_invested_amount_growth
    lo, hi = base_rate, base_rate + step_up_max_delta_pp
    best: tuple[float, GoalPlanningOutput] | None = None
    for _ in range(8):
        mid = (lo + hi) / 2
        new_inp = inp.model_copy(deep=True)
        new_inp.assumptions = inp.assumptions.model_copy(update={"annual_invested_amount_growth": mid})
        new_out = compute_full_projection(new_inp)
        if _is_feasible(new_out):
            best = (mid, new_out)
            hi = mid
        else:
            lo = mid
    if best is None:
        return None
    rate, target_out = best
    delta = rate - base_rate
    confidence = "high" if delta <= 0.05 else ("medium" if delta <= 0.10 else "low")
    return Lever(
        description=f"Increase step-up rate from {base_rate:.1%} to {rate:.1%}",
        action=NumericOverride(kind="numeric", key="step_up_rate", value=rate),
        projected_outcome=target_out.headline,
        confidence=confidence,
    )


def generate_lever_f_reduce_expense(
    inp: GoalPlanningInput, baseline_out: GoalPlanningOutput,
    reduce_pct_list: tuple[float, ...] = (0.05, 0.10, 0.15),
) -> Lever | None:
    """Lever F: try -5/-10/-15% on monthly_household_expense. Confidence: low always."""
    if _is_feasible(baseline_out):
        return None
    base = inp.profile.monthly_household_expense
    for pct in reduce_pct_list:
        new_expense = base * (1 - pct)
        new_inp = inp.model_copy(deep=True)
        new_inp.profile = inp.profile.model_copy(update={"monthly_household_expense": new_expense})
        new_out = compute_full_projection(new_inp)
        if _is_feasible(new_out):
            return Lever(
                description=f"Reduce monthly household expense by {int(pct*100)}% (from ₹{base:,.0f} to ₹{new_expense:,.0f})",
                action=NumericOverride(
                    kind="numeric", key="monthly_household_expense", value=new_expense,
                ),
                projected_outcome=new_out.headline,
                confidence="low",
            )
    return None


CATEGORY_PRIORITY = {
    "A": 1.0,    # SIP increase — most actionable
    "B": 0.9,    # defer goal — soft change
    "E": 0.85,   # step-up — sustainable
    "C": 0.6,    # reduce target — affects life
    "D": 0.5,    # delay retirement — affects life
    "F": 0.4,    # cut expense — hardest
}
CONFIDENCE_WEIGHT = {"high": 1.0, "medium": 0.7, "low": 0.4}


def _score_lever(lever: Lever, category: str) -> float:
    return CONFIDENCE_WEIGHT[lever.confidence] * CATEGORY_PRIORITY[category]


def propose_levers(
    inp: GoalPlanningInput, baseline_out: GoalPlanningOutput, max_count: int = 3,
) -> list[Lever]:
    """Generate up to 6 levers (A-F), score, return top N (default 3).

    Returns [] when the plan is already feasible OR when no lever in the
    search bounds closes the gap. The caller (chat surface) handles the
    "nothing in our search range works" messaging.
    """
    if _is_feasible(baseline_out):
        return []
    candidates: list[tuple[Lever, str]] = []
    if (l := generate_lever_a_increase_sip(inp, baseline_out)):
        candidates.append((l, "A"))
    if (l := generate_lever_b_defer_goal(inp, baseline_out)):
        candidates.append((l, "B"))
    if (l := generate_lever_c_reduce_target(inp, baseline_out)):
        candidates.append((l, "C"))
    if (l := generate_lever_d_retirement_age(inp, baseline_out)):
        candidates.append((l, "D"))
    if (l := generate_lever_e_step_up(inp, baseline_out)):
        candidates.append((l, "E"))
    if (l := generate_lever_f_reduce_expense(inp, baseline_out)):
        candidates.append((l, "F"))

    candidates.sort(key=lambda lc: _score_lever(lc[0], lc[1]), reverse=True)
    return [c[0] for c in candidates[:max_count]]
