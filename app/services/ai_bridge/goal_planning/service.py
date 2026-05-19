"""Goal-planning bridge service: ORM → engine+agent → facts_pack for the formatter.

Wraps ``run_cashflow_statement`` (the LangGraph agent that runs the engine,
proposes levers when relevant, and generates an LLM narrative summary) and
projects the resulting snapshot into a curated ``facts_pack`` dict for the
shared chat formatter.

Money convention follows the formatter's house style: every rupee numeric is
paired with a sibling ``_indian`` string already converted to Indian notation,
which the formatter LLM is instructed to copy verbatim.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from app.services.ai_bridge.common import ensure_ai_agents_path
from app.services.ai_bridge.goal_planning.input_builder import (
    build_goal_planning_input_for_user,
)

ensure_ai_agents_path()

from common import format_inr_indian
from cashflow_statement import GoalPlanningRequest, GoalPlanningSnapshot
from cashflow_statement.agent.graph import run_cashflow_statement
from cashflow_statement.summarizer import _build_facts as _summarizer_facts


@dataclass(slots=True)
class GoalPlanningRunOutcome:
    """Everything the chat handler needs from a single goal-planning turn."""
    snapshot: GoalPlanningSnapshot
    facts_pack: dict[str, Any]
    fallback_text: str


def _build_facts_pack(
    snapshot: GoalPlanningSnapshot,
    validation_issues: list[str],
) -> dict[str, Any]:
    """Curated facts the formatter LLM is allowed to cite.

    Most of the structure is reused from ``cashflow_statement.summarizer._build_facts``
    so the Indian-notation discipline stays consistent across the dev viewer
    summary and the chat formatter.
    """
    facts = _summarizer_facts(snapshot)

    # Always emit verdict-shape goals so the formatter LLM sees one contract.
    # When the agent's summary is present, use its vetted goal bullets;
    # otherwise synthesize the same shape from raw engine goals.
    if snapshot.summary is not None:
        s = snapshot.summary
        facts["goals"] = [
            {
                "name": gb.name,
                "verdict": gb.verdict,
                "headline_amount_indian": gb.headline_amount,
                "note": gb.note,
            }
            for gb in s.goals
        ]
        facts["narrative"] = {
            "top_line": s.top_line,
            "retirement_note": s.retirement_note,
            "cashflow_note": s.cashflow_note,
            "risks": s.risks,
        }
    else:
        facts["goals"] = [
            {
                "name": g.name,
                "verdict": "funded" if g.is_funded else "unfunded",
                "headline_amount_indian": format_inr_indian(
                    g.corpus_required_fv if g.is_funded else g.shortfall_fv
                ),
                "note": "",
            }
            for g in snapshot.goals
        ]
        facts["narrative"] = None

    # Levers → next-step suggestions. Each lever has been proved feasible
    # against the engine, so the formatter can cite them with confidence.
    facts["next_steps"] = [
        {
            "description": lever.description,
            "confidence": lever.confidence,
        }
        for lever in snapshot.levers
    ]

    facts["validation_issues"] = validation_issues
    return facts


def _build_fallback_brief(snapshot: GoalPlanningSnapshot) -> str:
    """Deterministic short reply used when the formatter LLM fails.

    Prefers the LLM-generated ``top_line`` (already vetted, no arithmetic);
    falls back to an engine-only one-liner when the summary is absent.
    """
    if snapshot.summary is not None:
        return snapshot.summary.top_line

    h = snapshot.headline
    if h.total_shortfall_fv > 0:
        return (
            f"Your plan shows a shortfall of "
            f"{format_inr_indian(h.total_shortfall_fv)} across "
            f"{h.number_of_goals} goal(s). Ask me how to close it."
        )
    return (
        f"Your plan funds all {h.number_of_goals} goal(s); "
        f"projected closing corpus is {format_inr_indian(h.corpus_closing)}."
    )


async def compute_goal_planning_snapshot(
    *,
    user: Any,
    user_question: str,
    chat_session_id: str,
    anchor_date: date,
) -> GoalPlanningRunOutcome:
    """Run the goal-planning agent for one chat turn and return its outcome.

    Raises ``ValueError("missing_date_of_birth")`` from the input builder when
    DOB is absent — chat handler is expected to catch and surface a clean
    "please complete your profile" message.
    """
    inp, debug = build_goal_planning_input_for_user(user, anchor_date)
    validation_issues: list[str] = list(debug.get("validation_issues", []))

    request = GoalPlanningRequest(
        chat_session_id=chat_session_id,
        user_question=user_question,
        baseline_input=inp,
        anchor_date=anchor_date,
        detail_level="default",
    )
    snapshot = await run_cashflow_statement(request)

    return GoalPlanningRunOutcome(
        snapshot=snapshot,
        facts_pack=_build_facts_pack(snapshot, validation_issues),
        fallback_text=_build_fallback_brief(snapshot),
    )
