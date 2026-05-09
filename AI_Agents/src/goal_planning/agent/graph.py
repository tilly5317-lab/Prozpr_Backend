"""StateGraph definition + compile + run_goal_planning entry."""
from __future__ import annotations
import asyncio
from typing import Any, Annotated

from langchain_core.messages import HumanMessage, ToolMessage
from langchain_core.tools import tool, InjectedToolCallId
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode, InjectedState
from langgraph.types import Command

from goal_planning.agent.state import AgentState
from goal_planning.agent.nodes import ingest_baseline_node, make_agent_node, should_continue
from goal_planning.agent.tools import (
    extract_financial_event_impl, apply_override_impl, clear_overrides_impl,
    mutate_goal_impl, compute_projection_impl, propose_levers_impl,
)
from goal_planning.models import (
    GoalPlanningOutput, GoalPlanningRequest, GoalPlanningSnapshot,
    OverrideSpec, TurnAction,
)


# === Tool wrappers ===
#
# Each wrapper returns a Command(update={...}, messages=[ToolMessage(...)]) so that
# state mutations made by the *_impl functions propagate back to the outer graph state.
# Without this, InjectedState gives the impl a snapshot whose mutations are discarded.


@tool
def extract_financial_event(
    description: str,
    state: Annotated[AgentState, InjectedState],
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """Parse a natural-language description of a financial goal, property purchase, or one-off cashflow."""
    summary, event = asyncio.run(extract_financial_event_impl(description, state))
    new_action = TurnAction(
        tool_name="extract_financial_event",
        arguments={"description": description},
        summary=summary,
    )
    update: dict[str, Any] = {
        "captured_goals": state["captured_goals"],
        "captured_properties": state["captured_properties"],
        "captured_cashflows": state["captured_cashflows"],
        "captured_mutations": state["captured_mutations"],
        "dirty": state["dirty"],
        "error_log": state["error_log"],
        "actions_taken_this_turn": [*state["actions_taken_this_turn"], new_action],
        "messages": [ToolMessage(content=summary, tool_call_id=tool_call_id)],
    }
    if event is not None:
        update["extracted_events_this_turn"] = [
            *state["extracted_events_this_turn"], event,
        ]
    return Command(update=update)


@tool
def apply_override(
    override: dict,
    state: Annotated[AgentState, InjectedState],
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """Stage a what-if change to a parameter (income, expense, SIP, rate)."""
    from pydantic import TypeAdapter
    override_input = override
    parsed = TypeAdapter(OverrideSpec).validate_python(override)
    summary = apply_override_impl(parsed, state)
    new_action = TurnAction(
        tool_name="apply_override",
        arguments={"override": override_input},
        summary=summary,
    )
    return Command(update={
        "accumulated_overrides": state["accumulated_overrides"],
        "dirty": state["dirty"],
        "actions_taken_this_turn": [*state["actions_taken_this_turn"], new_action],
        "messages": [ToolMessage(content=summary, tool_call_id=tool_call_id)],
    })


@tool
def clear_overrides(
    keys: list[str] | None,
    state: Annotated[AgentState, InjectedState],
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """Clear staged overrides (all if keys=None, or specific keys)."""
    summary = clear_overrides_impl(keys, state)
    new_action = TurnAction(
        tool_name="clear_overrides",
        arguments={"keys": keys},
        summary=summary,
    )
    return Command(update={
        "accumulated_overrides": state["accumulated_overrides"],
        "dirty": state["dirty"],
        "actions_taken_this_turn": [*state["actions_taken_this_turn"], new_action],
        "messages": [ToolMessage(content=summary, tool_call_id=tool_call_id)],
    })


@tool
def mutate_goal(
    op: str,
    goal_name: str,
    fields: dict[str, Any],
    state: Annotated[AgentState, InjectedState],
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """Add/remove/update a goal (incl. retirement)."""
    summary = mutate_goal_impl(op, goal_name, fields, state)
    new_action = TurnAction(
        tool_name="mutate_goal",
        arguments={"op": op, "goal_name": goal_name, "fields": fields},
        summary=summary,
    )
    return Command(update={
        "captured_mutations": state["captured_mutations"],
        "dirty": state["dirty"],
        "actions_taken_this_turn": [*state["actions_taken_this_turn"], new_action],
        "messages": [ToolMessage(content=summary, tool_call_id=tool_call_id)],
    })


@tool
def compute_projection(
    state: Annotated[AgentState, InjectedState],
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """Run the goal-planning engine. Idempotent."""
    summary = compute_projection_impl(state)
    new_action = TurnAction(tool_name="compute_projection", arguments={}, summary=summary)
    return Command(update={
        "last_output": state["last_output"],
        "dirty": state["dirty"],
        "actions_taken_this_turn": [*state["actions_taken_this_turn"], new_action],
        "messages": [ToolMessage(content=summary, tool_call_id=tool_call_id)],
    })


@tool
def propose_levers(
    state: Annotated[AgentState, InjectedState],
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """Generate up to 3 deterministic recommendations to close shortfalls."""
    summary = propose_levers_impl(state)
    new_action = TurnAction(tool_name="propose_levers", arguments={}, summary=summary)
    return Command(update={
        "last_levers": state["last_levers"],
        "actions_taken_this_turn": [*state["actions_taken_this_turn"], new_action],
        "messages": [ToolMessage(content=summary, tool_call_id=tool_call_id)],
    })


TOOLS = [
    extract_financial_event, apply_override, clear_overrides,
    mutate_goal, compute_projection, propose_levers,
]


def build_graph(checkpointer=None, model: str = "claude-sonnet-4-6"):
    workflow = StateGraph(AgentState)
    workflow.add_node("ingest_baseline", ingest_baseline_node)
    workflow.add_node("agent", make_agent_node(TOOLS, model=model))
    workflow.add_node("tools", ToolNode(TOOLS))

    workflow.set_entry_point("ingest_baseline")
    workflow.add_edge("ingest_baseline", "agent")
    workflow.add_conditional_edges("agent", should_continue, {"tools": "tools", END: END})
    workflow.add_edge("tools", "agent")

    return workflow.compile(checkpointer=checkpointer)


_compiled_graph = None


def get_compiled_graph():
    """Singleton — instantiate once at first use."""
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = build_graph(checkpointer=MemorySaver())
    return _compiled_graph


async def run_goal_planning(request: GoalPlanningRequest) -> GoalPlanningSnapshot:
    """Public entry point: customer question + baseline → structured snapshot for the responder LLM.

    The agent (Haiku-driven LangGraph) routes to tools internally and produces a structured
    snapshot. There is NO customer-facing narrative here — the cross-module responder LLM
    writes that, using this snapshot as input.
    """
    config = {
        "configurable": {"thread_id": request.chat_session_id},
        "recursion_limit": 15,
    }
    state_update = {
        "messages": [HumanMessage(content=request.user_question)],
        "baseline_input": request.baseline_input,
        "anchor_date": request.anchor_date,
        "accumulated_overrides": [],
        "captured_goals": [],
        "captured_properties": [],
        "captured_cashflows": [],
        "captured_mutations": [],
        "last_output": None,
        "last_levers": [],
        "actions_taken_this_turn": [],
        "extracted_events_this_turn": [],
        "dirty": False,
        "error_log": [],
    }
    graph = get_compiled_graph()
    try:
        final = await graph.ainvoke(state_update, config)  # type: ignore[arg-type]
    except Exception:
        # Recursion limit hit or unexpected graph error — return an "errors" snapshot
        return _build_error_snapshot(request, reason="recursion_limit_or_graph_error")

    return _build_snapshot_from_state(request, final)


def _build_snapshot_from_state(
    request: GoalPlanningRequest, final_state: dict,
) -> GoalPlanningSnapshot:
    """Build the snapshot from the final agent state."""
    out: GoalPlanningOutput | None = final_state.get("last_output")
    if out is None:
        # Agent didn't run compute_projection. Build an empty snapshot using a fresh engine call
        # over the baseline so the responder still has structured data to read.
        # Apply any captured-state-merge if needed; for now use bare baseline.
        from goal_planning.engine import compute_full_projection
        out = compute_full_projection(request.baseline_input)

    # Construct snapshot — inherits all GoalPlanningOutput fields + adds per-turn fields
    return GoalPlanningSnapshot(
        # All GoalPlanningOutput fields:
        engine_version=out.engine_version,
        computed_at=out.computed_at,
        input_echo=out.input_echo,
        headline=out.headline,
        retirement=out.retirement,
        goals=out.goals,
        goal_property_details=out.goal_property_details,
        one_off_outflow_status=out.one_off_outflow_status,
        annual_cashflow=out.annual_cashflow,
        fund_flow_summary=out.fund_flow_summary,
        derived_stats=out.derived_stats,
        monthly_cashflow=out.monthly_cashflow if request.detail_level == "full" else None,
        nfa_monthly_series=out.nfa_monthly_series if request.detail_level == "full" else None,
        mortgage_amortizations=out.mortgage_amortizations if request.detail_level == "full" else None,
        warnings=out.warnings,
        # Snapshot-only fields:
        extracted_events_this_turn=final_state.get("extracted_events_this_turn", []),
        actions_taken_this_turn=final_state.get("actions_taken_this_turn", []),
        levers=final_state.get("last_levers", []),
        validation_issues=[],   # Phase 1 already exposes via validate_input_only; not populated by the agent path
        error_log=final_state.get("error_log", []),
    )


def _build_error_snapshot(request: GoalPlanningRequest, reason: str) -> GoalPlanningSnapshot:
    """Fallback snapshot when the graph itself fails (e.g., recursion limit)."""
    from goal_planning.engine import compute_full_projection
    out = compute_full_projection(request.baseline_input)
    return GoalPlanningSnapshot(
        engine_version=out.engine_version,
        computed_at=out.computed_at,
        input_echo=out.input_echo,
        headline=out.headline,
        retirement=out.retirement,
        goals=out.goals,
        goal_property_details=out.goal_property_details,
        one_off_outflow_status=out.one_off_outflow_status,
        annual_cashflow=out.annual_cashflow,
        fund_flow_summary=out.fund_flow_summary,
        derived_stats=out.derived_stats,
        monthly_cashflow=out.monthly_cashflow if request.detail_level == "full" else None,
        nfa_monthly_series=out.nfa_monthly_series if request.detail_level == "full" else None,
        mortgage_amortizations=out.mortgage_amortizations if request.detail_level == "full" else None,
        warnings=out.warnings,
        extracted_events_this_turn=[],
        actions_taken_this_turn=[],
        levers=[],
        validation_issues=[],
        error_log=[f"agent_failure: {reason}"],
    )
