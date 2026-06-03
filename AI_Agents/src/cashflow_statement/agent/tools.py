"""The six LangGraph agent tools — pure-Python impls plus their @tool wrappers.

Each tool has an *implementation function* (foo_impl): a pure-Python operation on
AgentState, unit-testable without LangGraph. The @tool-decorated wrappers (bottom
of this file) adapt those impls to LangGraph — they read InjectedState, call the
impl, and return a Command that propagates state mutations plus a ToolMessage.
`TOOLS` is the list bound to the agent node and the ToolNode.
"""
from __future__ import annotations
import asyncio
from typing import Annotated, Any

from langchain_core.messages import ToolMessage
from langchain_core.tools import tool, InjectedToolCallId
from langgraph.prebuilt import InjectedState
from langgraph.types import Command

from cashflow_statement.models import (
    OverrideSpec, GoalMutation, ExtractedFinancialEvent, ExtractionError,
    ExtractedGoal, ExtractedProperty, ExtractedCashflow, ExtractedMutation,
    TurnAction,
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
    # Every OverrideSpec variant (NumericOverride / RateOverride) has a `key`.
    return f"Override staged: {override.key}={override.value}. Run compute_projection to see impact."


def clear_overrides_impl(keys: list[str] | None, state: AgentState) -> str:
    """Clear all overrides (keys=None) or specific keys."""
    if keys is None:
        n = len(state["accumulated_overrides"])
        state["accumulated_overrides"] = []
        state["dirty"] = True
        return f"Cleared {n} override(s)."
    before = len(state["accumulated_overrides"])
    state["accumulated_overrides"] = [
        o for o in state["accumulated_overrides"] if o.key not in keys
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

    # Apply overrides (last-write-wins per key). Every OverrideSpec has a `key`.
    by_key: dict[str, OverrideSpec] = {}
    for o in state["accumulated_overrides"]:
        by_key[o.key] = o

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
    lines = [
        f"{i + 1}. {lever.description} (confidence: {lever.confidence})"
        for i, lever in enumerate(levers)
    ]
    return "Recommended levers:\n" + "\n".join(lines)


# === @tool wrappers ===
#
# Each wrapper returns a Command(update={...}, messages=[ToolMessage(...)]) so that
# state mutations made by the *_impl functions propagate back to the outer graph state.
# Without this, InjectedState gives the impl a snapshot whose mutations are discarded.
#
# The shared TurnAction + Command construction lives in _build_tool_command below
# so the six wrappers don't repeat the same five lines of audit-logging boilerplate.


def _build_tool_command(
    state: AgentState,
    tool_name: str,
    arguments: dict[str, Any],
    summary: str,
    tool_call_id: str,
    state_updates: dict[str, Any],
) -> Command:
    """Build a Command that merges state updates, appends a TurnAction, and emits a ToolMessage."""
    new_action = TurnAction(
        tool_name=tool_name,
        arguments=arguments,
        summary=summary,
    )
    return Command(update={
        **state_updates,
        "actions_taken_this_turn": [*state["actions_taken_this_turn"], new_action],
        "messages": [ToolMessage(content=summary, tool_call_id=tool_call_id)],
    })


@tool
def extract_financial_event(
    description: str,
    state: Annotated[AgentState, InjectedState],
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """Parse a natural-language description of a financial goal, property purchase, or one-off cashflow."""
    summary, event = asyncio.run(extract_financial_event_impl(description, state))
    state_updates: dict[str, Any] = {
        "captured_goals": state["captured_goals"],
        "captured_properties": state["captured_properties"],
        "captured_cashflows": state["captured_cashflows"],
        "captured_mutations": state["captured_mutations"],
        "dirty": state["dirty"],
        "error_log": state["error_log"],
    }
    if event is not None:
        state_updates["extracted_events_this_turn"] = [
            *state["extracted_events_this_turn"], event,
        ]
    return _build_tool_command(
        state, "extract_financial_event", {"description": description},
        summary, tool_call_id, state_updates,
    )


@tool
def apply_override(
    override: dict,
    state: Annotated[AgentState, InjectedState],
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """Stage a what-if change to a parameter (income, expense, SIP, rate)."""
    from pydantic import TypeAdapter
    parsed = TypeAdapter(OverrideSpec).validate_python(override)
    summary = apply_override_impl(parsed, state)
    return _build_tool_command(
        state, "apply_override", {"override": override},
        summary, tool_call_id,
        state_updates={
            "accumulated_overrides": state["accumulated_overrides"],
            "dirty": state["dirty"],
        },
    )


@tool
def clear_overrides(
    keys: list[str] | None,
    state: Annotated[AgentState, InjectedState],
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """Clear staged overrides (all if keys=None, or specific keys)."""
    summary = clear_overrides_impl(keys, state)
    return _build_tool_command(
        state, "clear_overrides", {"keys": keys},
        summary, tool_call_id,
        state_updates={
            "accumulated_overrides": state["accumulated_overrides"],
            "dirty": state["dirty"],
        },
    )


@tool
def mutate_goal(
    op: str,
    goal_name: str,
    fields: dict[str, Any],
    state: Annotated[AgentState, InjectedState],
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """Remove/update a goal (incl. retirement)."""
    summary = mutate_goal_impl(op, goal_name, fields, state)
    return _build_tool_command(
        state, "mutate_goal",
        {"op": op, "goal_name": goal_name, "fields": fields},
        summary, tool_call_id,
        state_updates={
            "captured_mutations": state["captured_mutations"],
            "dirty": state["dirty"],
        },
    )


@tool
def compute_projection(
    state: Annotated[AgentState, InjectedState],
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """Run the goal-planning engine. Idempotent."""
    summary = compute_projection_impl(state)
    return _build_tool_command(
        state, "compute_projection", {},
        summary, tool_call_id,
        state_updates={
            "last_output": state["last_output"],
            "dirty": state["dirty"],
        },
    )


@tool
def propose_levers(
    state: Annotated[AgentState, InjectedState],
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """Generate up to 3 deterministic recommendations to close shortfalls."""
    summary = propose_levers_impl(state)
    return _build_tool_command(
        state, "propose_levers", {},
        summary, tool_call_id,
        state_updates={
            "last_levers": state["last_levers"],
        },
    )


TOOLS = [
    extract_financial_event, apply_override, clear_overrides,
    mutate_goal, compute_projection, propose_levers,
]
