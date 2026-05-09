"""StateGraph definition + compile + run_goal_planning_agent entry."""
from __future__ import annotations
import asyncio
from datetime import date
from typing import Any, Annotated

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode, InjectedState

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

@tool
def extract_financial_event(
    description: str,
    state: Annotated[AgentState, InjectedState],
) -> str:
    """Parse a natural-language description of a financial goal, property purchase, or one-off cashflow."""
    return asyncio.run(extract_financial_event_impl(description, state))


@tool
def apply_override(
    override: dict,
    state: Annotated[AgentState, InjectedState],
) -> str:
    """Stage a what-if change to a parameter (income, expense, SIP, rate)."""
    from pydantic import TypeAdapter
    parsed = TypeAdapter(OverrideSpec).validate_python(override)
    return apply_override_impl(parsed, state)


@tool
def clear_overrides(
    keys: list[str] | None,
    state: Annotated[AgentState, InjectedState],
) -> str:
    """Clear staged overrides (all if keys=None, or specific keys)."""
    return clear_overrides_impl(keys, state)


@tool
def mutate_goal(
    op: str,
    goal_name: str,
    fields: dict[str, Any],
    state: Annotated[AgentState, InjectedState],
) -> str:
    """Add/remove/update a goal (incl. retirement)."""
    return mutate_goal_impl(op, goal_name, fields, state)


@tool
def compute_projection(
    state: Annotated[AgentState, InjectedState],
) -> str:
    """Run the goal-planning engine. Idempotent."""
    return compute_projection_impl(state)


@tool
def propose_levers(
    state: Annotated[AgentState, InjectedState],
) -> str:
    """Generate up to 3 deterministic recommendations to close shortfalls."""
    return propose_levers_impl(state)


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
    state_update = {
        "messages": [HumanMessage(content=user_message)],
        "baseline_input": baseline_input,
        "anchor_date": anchor_date,
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
