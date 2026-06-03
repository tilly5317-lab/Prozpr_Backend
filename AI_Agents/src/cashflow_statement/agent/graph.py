"""StateGraph definition + compile + run_cashflow_statement entry."""
from __future__ import annotations

import logging

from langchain_core.messages import HumanMessage
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode

from cashflow_statement.agent.state import AgentState
from cashflow_statement.agent.nodes import (
    ingest_baseline_node, make_agent_node, should_continue, finalize_node,
)
from cashflow_statement.agent.tools import TOOLS
from cashflow_statement.models import (
    GoalPlanningOutput, GoalPlanningRequest, GoalPlanningSnapshot,
)


logger = logging.getLogger(__name__)


# Max agent ↔ tools loop iterations per run. If the LLM keeps calling tools
# without converging, the graph errors out at this cap and we return the
# fallback error-snapshot. Tune up if customers regularly hit it.
AGENT_RECURSION_LIMIT = 15


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
    """Singleton — instantiate once at first use.

    checkpointer=None: each invocation gets a fresh state — no in-memory
    checkpoint accumulation across sessions. The graph receives a full
    state_update on every call so multi-turn checkpointing is unnecessary.
    """
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = build_graph(checkpointer=None)
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
    except Exception as e:
        # Recursion limit hit OR any unexpected graph error. Log the actual
        # exception type + traceback so real bugs (e.g. a KeyError in a node)
        # aren't silently filed as just a recursion problem. Surface the class
        # name in the snapshot's error_log too so the caller sees what failed.
        logger.exception("cashflow_statement graph failed")
        return _build_error_snapshot(
            request,
            reason=f"graph_failure: {e.__class__.__name__}: {e}",
        )

    return _build_snapshot_from_state(request, final)


def _snapshot_from_output(
    request: GoalPlanningRequest,
    out: GoalPlanningOutput,
    *,
    extracted_events_this_turn: list,
    actions_taken_this_turn: list,
    levers: list,
    error_log: list,
    summary=None,
) -> GoalPlanningSnapshot:
    """Assemble a GoalPlanningSnapshot from an engine output plus the per-turn
    audit fields. Shared by the success and error-fallback paths so the engine
    field mapping lives in exactly one place."""
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
        extracted_events_this_turn=extracted_events_this_turn,
        actions_taken_this_turn=actions_taken_this_turn,
        levers=levers,
        validation_issues=[],
        error_log=error_log,
        summary=summary,
    )


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

    return _snapshot_from_output(
        request, out,
        extracted_events_this_turn=final_state.get("extracted_events_this_turn", []),
        actions_taken_this_turn=final_state.get("actions_taken_this_turn", []),
        levers=final_state.get("last_levers", []),
        error_log=final_state.get("error_log", []),
        summary=final_state.get("last_summary"),
    )


def _build_error_snapshot(request: GoalPlanningRequest, reason: str) -> GoalPlanningSnapshot:
    """Fallback snapshot when the graph itself fails (e.g., recursion limit)."""
    from cashflow_statement.engine import compute_full_projection
    out = compute_full_projection(request.baseline_input)
    return _snapshot_from_output(
        request, out,
        extracted_events_this_turn=[],
        actions_taken_this_turn=[],
        levers=[],
        error_log=[f"agent_failure: {reason}"],
    )
