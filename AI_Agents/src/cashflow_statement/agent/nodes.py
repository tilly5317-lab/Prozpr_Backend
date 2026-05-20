"""LangGraph nodes for cashflow_statement agent."""
from __future__ import annotations

from datetime import date

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessage, SystemMessage
from langgraph.graph import END

from cashflow_statement.agent.state import AgentState
from cashflow_statement.agent.prompts import SYSTEM_PROMPT
from cashflow_statement.models import GoalPlanningInput
from cashflow_statement.summarizer import summarize_plan


# Default agent model — overridable via config (Phase 4 will add config.py)
AGENT_MODEL_DEFAULT = "claude-haiku-4-5-20251001"


def _fmt_inr(amount: float) -> str:
    """Format a rupee amount in Indian conventions: ₹X.XXCr / ₹X.XXL / ₹X,XXX."""
    if abs(amount) >= 1_00_00_000:  # 1 crore
        return f"₹{amount/1_00_00_000:.2f}Cr"
    if abs(amount) >= 1_00_000:  # 1 lakh
        return f"₹{amount/1_00_000:.2f}L"
    return f"₹{amount:,.0f}"


def _years_until(today: date, target: date) -> str:
    delta = (target - today).days / 365.25
    return f"{delta:.1f}y"


def _format_baseline_summary(inp: GoalPlanningInput, anchor: date) -> str:
    """Render a compact summary of what's loaded for the system prompt."""
    p = inp.profile
    r = inp.retirement
    age = (anchor - r.date_of_birth).days // 365 if r.date_of_birth else None
    corpus = p.financial_assets - p.financial_liabilities_excl_mortgage

    lines: list[str] = []

    lines.append("PROFILE")
    if age is not None:
        lines.append(f"- Date of birth: {r.date_of_birth} (current age ~{age})")
    lines.append(f"- Retirement: planned at age {r.retirement_age} (~year {r.date_of_birth.year + r.retirement_age if r.date_of_birth else 'unknown'}); assumed lifespan {r.assumed_lifespan_years}")
    lines.append(f"- Annual income: {_fmt_inr(p.annual_income)} (effective tax rate {p.effective_tax_rate:.0%})")
    lines.append(f"- Net financial assets: {_fmt_inr(corpus)}  (assets {_fmt_inr(p.financial_assets)} − liabilities {_fmt_inr(p.financial_liabilities_excl_mortgage)})")
    lines.append(f"- Monthly household expense: {_fmt_inr(p.monthly_household_expense)}")
    if p.starting_monthly_investment:
        lines.append(f"- Monthly investment / SIP: {_fmt_inr(p.starting_monthly_investment)}")
    else:
        lines.append("- Monthly investment / SIP: not set")

    n_goals = 1 + len(inp.goal_properties) + len(inp.custom_goals)
    lines.append("")
    lines.append(f"GOALS ({n_goals})")
    lines.append(f"- retirement: corpus needed at age {r.retirement_age} (will be computed); assumed lifespan {r.assumed_lifespan_years}")
    for gp in inp.goal_properties:
        target = gp.target_pv if gp.target_pv else gp.target_fv
        mortgage_note = ""
        if gp.is_downpayment_only:
            if gp.downpayment_pct is not None:
                down_str = f"{gp.downpayment_pct:.0%} down"
            else:
                down_str = f"{_fmt_inr(gp.upfront_amount or 0)} down"
            tenure_str = f"{gp.mortgage_tenure_years}y" if gp.mortgage_tenure_years else "default tenure"
            rate_str = f"{gp.mortgage_interest_annual:.1%}" if gp.mortgage_interest_annual else "default rate"
            mortgage_note = f" — mortgage path: {down_str}, {tenure_str} at {rate_str}"
        lines.append(f"- {gp.name} (property): {_fmt_inr(target or 0)} in {gp.goal_date.year} ({_years_until(anchor, gp.goal_date)} away){mortgage_note}")
    for cg in inp.custom_goals:
        target = cg.goal_value_pv if cg.goal_value_pv else cg.corpus_required_fv
        units = "PV" if cg.goal_value_pv is not None else "FV"
        lines.append(f"- {cg.name} ({cg.goal_type.value}): {_fmt_inr(target or 0)} {units} in {cg.goal_date.year} ({_years_until(anchor, cg.goal_date)} away)")

    if inp.current_properties:
        active_mortgages = [
            cp for cp in inp.current_properties
            if cp.has_mortgage and cp.mortgage_emi and cp.mortgage_end_date
        ]
        if active_mortgages:
            lines.append("")
            lines.append(f"EXISTING MORTGAGES ({len(active_mortgages)})")
            for cp in active_mortgages:
                lines.append(f"- {cp.name}: {_fmt_inr(cp.mortgage_emi)}/month EMI through {cp.mortgage_end_date}")

    if inp.one_off_inflows or inp.one_off_outflows:
        lines.append("")
        lines.append("ONE-OFF CASHFLOWS")
        for e in inp.one_off_inflows:
            lines.append(f"- IN: {e.description} {_fmt_inr(e.amount)} on {e.date}")
        for e in inp.one_off_outflows:
            lines.append(f"- OUT: {e.description} {_fmt_inr(e.amount)} on {e.date}")

    return "\n".join(lines)


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

    # Always initialize ALL TypedDict fields so InjectedState validation in tools
    # sees a complete state on the first tool call. Pull existing values when present,
    # default to empty containers otherwise.
    return {
        "accumulated_overrides": valid,
        "captured_goals": state.get("captured_goals", []),
        "captured_properties": state.get("captured_properties", []),
        "captured_cashflows": state.get("captured_cashflows", []),
        "captured_mutations": state.get("captured_mutations", []),
        "last_levers": [],
        "actions_taken_this_turn": [],         # NEW: reset each turn
        "extracted_events_this_turn": [],      # NEW: reset each turn
        "last_output": None if invalidate else last_out,
        "last_summary": None,  # per-turn output; finalize_node refreshes it
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
        baseline_summary = _format_baseline_summary(
            state["baseline_input"], state["anchor_date"],
        )
        sys_msg = SystemMessage(content=SYSTEM_PROMPT.format(
            anchor_date=state["anchor_date"].isoformat(),
            baseline_summary=baseline_summary,
        ))
        response = llm.invoke([sys_msg] + state["messages"])
        return {"messages": [response]}

    return agent_node


def should_continue(state: AgentState) -> str:
    last = state["messages"][-1]
    if isinstance(last, AIMessage) and getattr(last, "tool_calls", None):
        return "tools"
    return "finalize"


def finalize_node(state: AgentState) -> dict:
    """End-of-turn: ensure engine output exists, then generate LLM summary.

    The agent's tool calls may have skipped `compute_projection` if it didn't
    need the numbers to answer. We still want a populated snapshot, so this
    node runs a baseline compute as a fallback and then summarises the result.
    Summary failures are logged to `error_log` and the turn proceeds — the
    snapshot's `summary` field just stays None.
    """
    from cashflow_statement.engine import compute_full_projection

    last_out = state.get("last_output")
    if last_out is None:
        last_out = compute_full_projection(state["baseline_input"])

    summary = None
    error_log = list(state.get("error_log", []))
    try:
        summary = summarize_plan(last_out, levers=state.get("last_levers", []))
    except Exception as e:
        error_log.append(f"summarize_plan_failed: {e}")

    return {
        "last_output": last_out,
        "last_summary": summary,
        "error_log": error_log,
    }
