"""LangGraph nodes for goal_planning agent."""
from __future__ import annotations

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessage, SystemMessage
from langgraph.graph import END

from goal_planning.agent.state import AgentState
from goal_planning.agent.prompts import SYSTEM_PROMPT


# Default agent model — overridable via config (Phase 4 will add config.py)
AGENT_MODEL_DEFAULT = "claude-sonnet-4-6"


def ingest_baseline_node(state: AgentState) -> dict:
    """Validate persisted overrides against fresh baseline; drop orphans; reset levers; check baseline diff."""
    valid = []
    dropped = []
    for o in state.get("accumulated_overrides", []):
        if hasattr(o, "property_name"):
            existing_names = {p.name.casefold() for p in state["baseline_input"].current_properties}
            existing_names |= {p.name.casefold() for p in state["baseline_input"].goal_properties}
            if o.property_name.casefold() not in existing_names:
                dropped.append(f"{o.kind}:{o.property_name}")
                continue
        valid.append(o)

    last_out = state.get("last_output")
    invalidate = (
        last_out is not None
        and last_out.input_echo.profile != state["baseline_input"].profile
    )

    return {
        "accumulated_overrides": valid,
        "last_levers": [],
        "last_output": None if invalidate else last_out,
        "dirty": bool(dropped) or invalidate,
        "error_log": [
            *(state.get("error_log", [])),
            *(f"Dropped orphaned override: {d}" for d in dropped),
        ],
    }


def make_agent_node(tools: list, model: str = AGENT_MODEL_DEFAULT):
    """Closure factory: bind tools and return the agent node fn."""
    llm = ChatAnthropic(model=model, temperature=0).bind_tools(tools)

    def agent_node(state: AgentState) -> dict:
        nfa = (
            state["baseline_input"].profile.financial_assets
            - state["baseline_input"].profile.financial_liabilities_excl_mortgage
        )
        sys_msg = SystemMessage(content=SYSTEM_PROMPT.format(
            anchor_date=state["anchor_date"].isoformat(),
            nfa_today=nfa,
        ))
        response = llm.invoke([sys_msg] + state["messages"])
        return {"messages": [response]}

    return agent_node


def should_continue(state: AgentState) -> str:
    last = state["messages"][-1]
    if isinstance(last, AIMessage) and getattr(last, "tool_calls", None):
        return "tools"
    return END
