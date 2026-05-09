"""Deterministic lever generators (A through G).

Each generator returns a Lever | None. None means the lever doesn't apply or can't close
the gap within its search bounds. Per spec §8.4 every lever asserts mid-horizon NFA
non-negativity.
"""
from __future__ import annotations
from datetime import date

from goal_planning.models import (
    GoalPlanningInput, GoalPlanningOutput, Lever, NumericOverride,
    GoalMutation, PropertyFieldOverride,
)
from goal_planning.engine import compute_full_projection


def _is_feasible(out: GoalPlanningOutput) -> bool:
    """Both is_overall_feasible AND min-NFA non-negative across the horizon."""
    if not out.headline.is_overall_feasible:
        return False
    if out.nfa_monthly_series:
        if any(r.nfa_close < 0 for r in out.nfa_monthly_series):
            return False
    return True


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

    base_sip = inp.profile.monthly_investment_next_12m or 1
    lo, hi = base_sip, base_sip * sip_max_multiplier
    best: tuple[float, GoalPlanningOutput] | None = None

    for _ in range(8):
        mid = (lo + hi) / 2
        new_inp = inp.model_copy(deep=True)
        new_inp.profile = inp.profile.model_copy(update={"monthly_investment_next_12m": mid})
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
            kind="numeric", key="monthly_investment_next_12m", value=target_sip,
        ),
        projected_outcome=target_out.headline,
        confidence=confidence,
    )
