"""StateGraph definition + compile + run_goal_planning_agent entry."""
from __future__ import annotations
import asyncio
from datetime import date
from typing import Any, Annotated

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
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
from goal_planning.agent.prompts import _RECURSION_LIMIT_MESSAGE
from goal_planning.engine import ENGINE_VERSION
from goal_planning.models import (
    GoalPlanningInput, GoalPlanningResponse, OverrideSpec,
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
    summary = asyncio.run(extract_financial_event_impl(description, state))
    return Command(update={
        "captured_goals": state["captured_goals"],
        "captured_properties": state["captured_properties"],
        "captured_cashflows": state["captured_cashflows"],
        "captured_mutations": state["captured_mutations"],
        "dirty": state["dirty"],
        "error_log": state["error_log"],
        "messages": [ToolMessage(content=summary, tool_call_id=tool_call_id)],
    })


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
    return Command(update={
        "accumulated_overrides": state["accumulated_overrides"],
        "dirty": state["dirty"],
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
    return Command(update={
        "accumulated_overrides": state["accumulated_overrides"],
        "dirty": state["dirty"],
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
    return Command(update={
        "captured_mutations": state["captured_mutations"],
        "dirty": state["dirty"],
        "messages": [ToolMessage(content=summary, tool_call_id=tool_call_id)],
    })


@tool
def compute_projection(
    state: Annotated[AgentState, InjectedState],
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """Run the goal-planning engine. Idempotent."""
    summary = compute_projection_impl(state)
    return Command(update={
        "last_output": state["last_output"],
        "dirty": state["dirty"],
        "messages": [ToolMessage(content=summary, tool_call_id=tool_call_id)],
    })


@tool
def propose_levers(
    state: Annotated[AgentState, InjectedState],
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """Generate up to 3 deterministic recommendations to close shortfalls."""
    summary = propose_levers_impl(state)
    return Command(update={
        "last_levers": state["last_levers"],
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


def extract_terminal_narrative(messages: list[BaseMessage]) -> str:
    """Walk backward to find the last AIMessage with no tool_calls."""
    for msg in reversed(messages):
        if isinstance(msg, AIMessage) and not getattr(msg, "tool_calls", None):
            content = msg.content
            return content if isinstance(content, str) else str(content)
    return "(no narrative)"


async def run_goal_planning_agent(
    user_message: str,
    baseline_input: GoalPlanningInput,
    chat_session_id: str,
    anchor_date: date,
) -> GoalPlanningResponse:
    config = {
        "configurable": {"thread_id": chat_session_id},
        "recursion_limit": 15,
    }
    # Initialize ALL TypedDict fields. ToolNode's InjectedState validates state shape
    # against the AgentState schema; missing keys (even those a node will set later)
    # raise validation errors during the first tool invocation.
    state_update = {
        "messages": [HumanMessage(content=user_message)],
        "baseline_input": baseline_input,
        "anchor_date": anchor_date,
        "accumulated_overrides": [],
        "captured_goals": [],
        "captured_properties": [],
        "captured_cashflows": [],
        "captured_mutations": [],
        "last_output": None,
        "last_levers": [],
        "dirty": False,
        "error_log": [],
    }
    graph = get_compiled_graph()
    try:
        final = await graph.ainvoke(state_update, config)  # type: ignore[arg-type]
    except Exception:
        return GoalPlanningResponse(
            engine_version=ENGINE_VERSION,
            output=None,
            narrative=_RECURSION_LIMIT_MESSAGE,
            levers=[],
        )

    return GoalPlanningResponse(
        engine_version=ENGINE_VERSION,
        output=final.get("last_output"),
        narrative=extract_terminal_narrative(final["messages"]),
        levers=final.get("last_levers", []),
    )
