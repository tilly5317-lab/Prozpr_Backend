"""StateGraph definition + compile + run_cashflow_statement entry."""
from __future__ import annotations
import asyncio
from typing import Any, Annotated

from langchain_core.messages import HumanMessage, ToolMessage
from langchain_core.tools import tool, InjectedToolCallId
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode, InjectedState
from langgraph.types import Command

from cashflow_statement.agent.state import AgentState
from cashflow_statement.agent.nodes import (
    ingest_baseline_node, make_agent_node, should_continue, finalize_node,
)
from cashflow_statement.agent.tools import (
    extract_financial_event_impl, apply_override_impl, clear_overrides_impl,
    mutate_goal_impl, compute_projection_impl, propose_levers_impl,
)
from cashflow_statement.models import (
    GoalPlanningOutput, GoalPlanningRequest, GoalPlanningSnapshot,
    OverrideSpec, TurnAction,
)


# Max agent ↔ tools loop iterations per run. If the LLM keeps calling tools
# without converging, the graph errors out at this cap and we return the
# fallback error-snapshot. Tune up if customers regularly hit it.
AGENT_RECURSION_LIMIT = 15


# === Tool wrappers ===
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


def build_graph(checkpointer=None, model: str = "claude-sonnet-4-6"):
    workflow = StateGraph(AgentState)
    workflow.add_node("ingest_baseline", ingest_baseline_node)
    workflow.add_node("agent", make_agent_node(TOOLS, model=model))
    workflow.add_node("tools", ToolNode(TOOLS))
    workflow.add_node("finalize", finalize_node)

    workflow.set_entry_point("ingest_baseline")
    workflow.add_edge("ingest_baseline", "agent")
    workflow.add_conditional_edges(
        "agent", should_continue,
        {"tools": "tools", "finalize": "finalize"},
    )
    workflow.add_edge("tools", "agent")
    workflow.add_edge("finalize", END)

    return workflow.compile(checkpointer=checkpointer)


_compiled_graph = None


def get_compiled_graph():
    """Singleton — instantiate once at first use."""
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = build_graph(checkpointer=MemorySaver())
    return _compiled_graph


async def run_cashflow_statement(request: GoalPlanningRequest) -> GoalPlanningSnapshot:
    """Public entry point: customer question + baseline → structured snapshot for the responder LLM.

    The agent (Haiku-driven LangGraph) routes to tools internally and produces a structured
    snapshot. There is NO customer-facing narrative here — the cross-module responder LLM
    writes that, using this snapshot as input.
    """
    config = {
        "configurable": {"thread_id": request.chat_session_id},
        "recursion_limit": AGENT_RECURSION_LIMIT,
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
        "last_summary": None,
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
        # finalize_node normally populates this; defensive fallback for paths
        # that bypass finalize (e.g., a future early-exit branch).
        from cashflow_statement.engine import compute_full_projection
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
        monthly_cashflow=out.monthly_cashflow if request.detail_level == "full" else None,
        warnings=out.warnings,
        extracted_events_this_turn=final_state.get("extracted_events_this_turn", []),
        actions_taken_this_turn=final_state.get("actions_taken_this_turn", []),
        levers=final_state.get("last_levers", []),
        validation_issues=[],
        error_log=final_state.get("error_log", []),
        summary=final_state.get("last_summary"),
    )


def _build_error_snapshot(request: GoalPlanningRequest, reason: str) -> GoalPlanningSnapshot:
    """Fallback snapshot when the graph itself fails (e.g., recursion limit)."""
    from cashflow_statement.engine import compute_full_projection
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
        monthly_cashflow=out.monthly_cashflow if request.detail_level == "full" else None,
        warnings=out.warnings,
        extracted_events_this_turn=[],
        actions_taken_this_turn=[],
        levers=[],
        validation_issues=[],
        error_log=[f"agent_failure: {reason}"],
    )
