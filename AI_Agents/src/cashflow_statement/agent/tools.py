"""Six tool impls for the LangGraph agent.

Each tool's *implementation function* (foo_impl) is a pure-Python operation on AgentState.
The @tool decorator wrappers live in agent/graph.py.
"""
from __future__ import annotations
from datetime import date
from typing import Any

from cashflow_statement.models import (
    OverrideSpec, GoalMutation, ExtractedFinancialEvent, ExtractionError,
    ExtractedGoal, ExtractedProperty, ExtractedCashflow, ExtractedMutation,
)
from cashflow_statement.agent.state import AgentState, CapturedCashflow
from cashflow_statement.agent.extractor import extract_event


# How many top-shortfall goals to surface in the bounded LLM summary after
# compute_projection. Higher → more context tokens, more granular signal.
TOP_UNDERFUNDED_GOALS_SHOWN = 3


async def extract_financial_event_impl(
    description: str, state: AgentState,
) -> tuple[str, ExtractedFinancialEvent | None]:
    """Returns (summary_for_llm, structured_event_for_audit_or_None_on_error)."""
    existing_names = (
        ["retirement"]
        + [g.name for g in state["baseline_input"].custom_goals]
        + [c.name for c in state["captured_goals"]]
        + [p.name for p in state["baseline_input"].goal_properties]
        + [p.name for p in state["captured_properties"]]
    )
    result = await extract_event(
        description, state["anchor_date"], existing_names,
        assumptions=state["baseline_input"].assumptions,
    )

    if isinstance(result, ExtractionError):
        state["error_log"].append(result.reason)
        return f"Could not extract: {result.reason}", None

    state["dirty"] = True
    if isinstance(result, ExtractedGoal):
        state["captured_goals"].append(result.goal)
        return f"Captured custom goal: {result.goal.name} on {result.goal.goal_date.isoformat()}", result
    if isinstance(result, ExtractedProperty):
        state["captured_properties"].append(result.property)
        if result.assumptions_used:
            return f"Captured property goal: {result.property.name}; assumptions used: {', '.join(result.assumptions_used)}", result
        return f"Captured property goal: {result.property.name}", result
    if isinstance(result, ExtractedCashflow):
        state["captured_cashflows"].append(CapturedCashflow(event=result.event, direction=result.direction))
        return f"Captured one-off {result.direction}flow: {result.event.description} ₹{result.event.amount:,.0f}", result
    if isinstance(result, ExtractedMutation):
        state["captured_mutations"].append(GoalMutation(
            kind="mutation", op=result.op, goal_name=result.goal_name, fields=result.fields,
        ))
        return f"Captured mutation on {result.goal_name}: {result.op}", result
    return "Unknown extraction kind", None


def apply_override_impl(override: OverrideSpec, state: AgentState) -> str:
    """Stage a parameter override."""
    state["accumulated_overrides"].append(override)
    state["dirty"] = True
    if hasattr(override, "key"):
        return f"Override staged: {override.key}={override.value}. Run compute_projection to see impact."
    return "Override staged. Run compute_projection to see impact."


def clear_overrides_impl(keys: list[str] | None, state: AgentState) -> str:
    """Clear all overrides (keys=None) or specific keys."""
    if keys is None:
        n = len(state["accumulated_overrides"])
        state["accumulated_overrides"] = []
        state["dirty"] = True
        return f"Cleared {n} override(s)."
    before = len(state["accumulated_overrides"])
    state["accumulated_overrides"] = [
        o for o in state["accumulated_overrides"] if getattr(o, "key", None) not in keys
    ]
    state["dirty"] = True
    return f"Cleared {before - len(state['accumulated_overrides'])} override(s)."


def mutate_goal_impl(op: str, goal_name: str, fields: dict[str, Any], state: AgentState) -> str:
    """Stage a goal mutation."""
    state["captured_mutations"].append(GoalMutation(
        kind="mutation", op=op, goal_name=goal_name, fields=fields,  # type: ignore[arg-type]
    ))
    state["dirty"] = True
    return f"Goal mutation staged: {op} '{goal_name}' with fields {list(fields.keys())}"


def _merge_state_into_input(state: AgentState):
    """Apply accumulated overrides + captures + mutations into baseline_input."""
    inp = state["baseline_input"].model_copy(deep=True)

    if state["captured_goals"]:
        inp.custom_goals = inp.custom_goals + state["captured_goals"]
    if state["captured_properties"]:
        inp.goal_properties = inp.goal_properties + state["captured_properties"]
    for cc in state["captured_cashflows"]:
        if cc.direction == "in":
            inp.one_off_inflows = inp.one_off_inflows + [cc.event]
        else:
            inp.one_off_outflows = inp.one_off_outflows + [cc.event]

    # Apply overrides (last-write-wins per key)
    by_key: dict[Any, OverrideSpec] = {}
    for o in state["accumulated_overrides"]:
        if hasattr(o, "key"):
            by_key[o.key] = o
        else:
            by_key[id(o)] = o

    for o in by_key.values():
        if o.kind == "numeric":
            if o.key == "starting_monthly_investment":
                inp.profile = inp.profile.model_copy(update={"starting_monthly_investment": o.value})
            elif o.key == "annual_income":
                inp.profile = inp.profile.model_copy(update={"annual_income": o.value})
            elif o.key == "monthly_household_expense":
                inp.profile = inp.profile.model_copy(update={"monthly_household_expense": o.value})
            elif o.key == "step_up_rate":
                inp.assumptions = inp.assumptions.model_copy(update={"annual_invested_amount_growth": o.value})
        elif o.kind == "rate":
            inp.assumptions = inp.assumptions.model_copy(update={o.key: o.value})

    # Apply mutations
    from cashflow_statement.agent.levers import _apply_goal_mutation
    for m in state["captured_mutations"]:
        if m.op == "remove":
            inp.custom_goals = [g for g in inp.custom_goals if g.name.casefold() != m.goal_name.casefold()]
        elif m.op == "update":
            inp = _apply_goal_mutation(inp, m.goal_name, m.fields)

    return inp


def compute_projection_impl(state: AgentState) -> str:
    """Run engine; idempotent (short-circuits if not dirty AND last_output exists)."""
    from cashflow_statement.engine import compute_full_projection

    if not state.get("dirty", True) and state.get("last_output") is not None:
        out = state["last_output"]
        feasible = out.headline.is_feasible
        return (
            f"Cached projection: feasible={feasible}, "
            f"shortfall=₹{out.headline.total_shortfall_fv:,.0f}, "
            f"closing corpus=₹{out.headline.corpus_closing:,.0f}"
        )

    inp = _merge_state_into_input(state)
    out = compute_full_projection(inp)
    state["last_output"] = out
    state["dirty"] = False
    return _summarize_output(out)


def _summarize_output(out) -> str:
    """Bounded summary string for the LLM (~300 tokens). Top-3 underfunded goals."""
    h = out.headline
    underfunded = sorted(
        [g for g in out.goals if g.shortfall_fv > 0],
        key=lambda g: g.shortfall_fv, reverse=True,
    )[:TOP_UNDERFUNDED_GOALS_SHOWN]
    lines = [
        f"Feasible: {h.is_feasible}",
        f"corpus today: ₹{h.corpus_today:,.0f}; closing corpus: ₹{h.corpus_closing:,.0f}",
        f"Total shortfall (FV): ₹{h.total_shortfall_fv:,.0f}",
        f"Retirement corpus needed: ₹{out.retirement.corpus_required_used:,.0f}",
    ]
    if underfunded:
        lines.append("Top underfunded goals:")
        for g in underfunded:
            lines.append(f"  - {g.name}: short by ₹{g.shortfall_fv:,.0f} (target ₹{g.corpus_required_fv:,.0f})")
    return "\n".join(lines)


def propose_levers_impl(state: AgentState) -> str:
    """Generate up to 7 levers; return top 3 ranked summary."""
    from cashflow_statement.agent.levers import propose_levers

    out = state.get("last_output")
    if out is None:
        return "Run compute_projection first to generate a baseline output."
    if out.headline.is_feasible:
        return "No shortfalls — no levers needed; plan is feasible."

    inp = _merge_state_into_input(state)
    levers = propose_levers(inp, out, max_count=3)
    state["last_levers"] = levers
    if not levers:
        return "No lever within the search bounds closes the gap."
    lines = [f"{i+1}. {l.description} (confidence: {l.confidence})" for i, l in enumerate(levers)]
    return "Recommended levers:\n" + "\n".join(lines)
