"""LLM summary of a `GoalPlanningOutput` for downstream customer-facing use.

Discipline (see common.format_inr_indian docstring): every rupee value in this
module is pre-formatted into Indian notation (₹X.XX lakh / crore) **before** it
reaches the LLM. Haiku is told to copy those strings verbatim and never to do
its own rupee arithmetic — this prevents the order-of-magnitude errors the
common helper exists to eliminate.

The output `PlanSummary` is the handoff payload a customer-facing LLM (or chat
agent) can consume directly without re-parsing the engine JSON.
"""
from __future__ import annotations

import json
from typing import Any

from anthropic import APIError
from langchain_anthropic import ChatAnthropic
from langchain_core.exceptions import OutputParserException
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field, ValidationError

from common import format_inr_indian
from cashflow_statement.models import GoalBullet, GoalPlanningOutput, Lever, PlanSummary


SUMMARIZER_MODEL = "claude-haiku-4-5-20251001"


SYSTEM_PROMPT = """\
You are a financial planning analyst summarizing an engine-computed goal plan
for an Indian retail investor. The engine has already done all the math; your
job is narrative, not calculation.

Rules — non-negotiable:
1. NEVER do rupee arithmetic. Every rupee value in the facts JSON is already
   formatted in Indian notation (e.g. "₹1.25 crore", "₹45 lakh"). Copy those
   strings VERBATIM into your output. Do not convert, round, or restate them
   in different units.
2. NEVER invent numbers. If a fact is not in the input, do not mention it.
3. Be concrete and neutral. No marketing language ("amazing", "great"). No
   second-person scolding. State what the plan shows.
4. Be brief. `top_line` is 1-2 sentences. Each note is 1 sentence. Each
   `GoalBullet.note` is 1 sentence.
5. Pick `verdict` per goal:
   - "funded" if `is_funded=true`
   - "unfunded" if `is_funded=false` and `funded_amount` is zero or near-zero
     relative to `corpus_required_fv`
   - "partially_funded" otherwise
6. For `GoalBullet.headline_amount`: use the goal's `corpus_required_fv_indian`
   when funded; use `shortfall_fv_indian` when not funded.
7. `risks` is a bulleted list of short phrases (not full paragraphs). 2-5
   items, fewer if the plan is healthy. Do NOT propose action items — those
   come from the deterministic lever engine, not from you.
8. Indian audience — refer to amounts as lakh/crore as already formatted, not
   million/billion.
"""


class _LLMNarrative(BaseModel):
    """The LLM's structured-output target. Mirrors PlanSummary minus
    `next_steps`, which is built deterministically from the lever list."""
    top_line: str = Field(
        description="1-2 sentence overall verdict — funded vs shortfall, biggest driver.",
    )
    retirement_note: str = Field(description="1 sentence on retirement adequacy.")
    goals: list[GoalBullet]
    cashflow_note: str = Field(
        description="1 sentence on income vs. expense / EMI / SIP capacity.",
    )
    risks: list[str] = Field(
        description="Bulleted concerns (e.g. concentration, near-term shortfall, EMI burden).",
    )


def _g(amount: Any) -> str:
    """Format a rupee amount in Indian notation; never returns None."""
    return format_inr_indian(amount) or "₹0"


def _build_facts(output: GoalPlanningOutput) -> dict:
    """Project the engine output into an LLM-friendly facts dict.

    Every rupee field is paired with a pre-formatted Indian-notation string.
    The raw float is also kept (named without `_indian`) so the LLM has access
    to magnitudes for ordering / comparison, but the prompt instructs it to
    quote only the `_indian` strings.
    """
    h = output.headline
    r = output.retirement
    ff = output.fund_flow_summary

    goals_facts = []
    for g in output.goals:
        goals_facts.append({
            "name": g.name,
            "goal_type": g.goal_type.value,
            "goal_date": g.goal_date.isoformat(),
            "is_funded": g.is_funded,
            "corpus_required_fv_indian": _g(g.corpus_required_fv),
            "funded_amount_indian": _g(g.funded_amount),
            "shortfall_fv_indian": _g(g.shortfall_fv),
            "goal_value_pv_indian": _g(g.goal_value_pv),
        })

    one_offs = []
    for o in output.one_off_outflow_status:
        one_offs.append({
            "description": o.description,
            "date": o.date.isoformat(),
            "amount_indian": _g(o.amount),
            "is_funded": o.is_funded,
            "shortfall_indian": _g(o.shortfall),
        })

    return {
        "headline": {
            "years_to_last_goal": h.years_to_last_goal,
            "number_of_goals": h.number_of_goals,
            "corpus_today_indian": _g(h.corpus_today),
            "total_corpus_required_today_indian": _g(h.total_corpus_required_today),
            "surplus_or_shortfall_today_indian": _g(h.surplus_or_shortfall_today),
            "corpus_closing_indian": _g(h.corpus_closing),
            "total_shortfall_fv_indian": _g(h.total_shortfall_fv),
            "total_funded_amount_indian": _g(h.total_funded_amount),
        },
        "retirement": {
            "retirement_date": r.retirement_date.isoformat(),
            "years_to_retirement": round(r.years_to_retirement, 1),
            "annual_household_expense_today_indian": _g(r.annual_household_expense_today),
            "corpus_required_at_retirement_indian": _g(r.corpus_required_used),
            "corpus_required_pv_today_indian": _g(r.corpus_required_pv_today),
        },
        "cashflow_horizon": {
            "corpus_opening_indian": _g(ff.corpus_opening),
            "total_investments_indian": _g(ff.total_investments),
            "total_roi_indian": _g(ff.total_roi),
            "total_one_off_in_indian": _g(ff.total_one_off_in),
            "total_one_off_out_indian": _g(ff.total_one_off_out),
            "total_goals_paid_indian": _g(ff.total_goals_paid),
            "corpus_closing_indian": _g(ff.corpus_closing),
        },
        "goals": goals_facts,
        "one_off_outflows": one_offs,
        "warnings": output.warnings,
    }


def summarize_plan(
    output: GoalPlanningOutput,
    levers: list[Lever] | None = None,
    model: str = SUMMARIZER_MODEL,
) -> PlanSummary:
    """Run the LLM narrative pass over an engine output and attach deterministic
    `next_steps` from the lever engine.

    `next_steps` is NOT LLM-generated: it is `[l.description for l in levers]`.
    Pass `levers=None` (or an empty list) when none have been computed; the
    summary will show no next steps rather than hallucinated ones.
    """
    facts = _build_facts(output)
    llm = ChatAnthropic(model=model, temperature=0)
    prompt = ChatPromptTemplate.from_messages([
        ("system", SYSTEM_PROMPT),
        ("human", "Plan facts (rupee values already in Indian notation):\n\n{facts}"),
    ])
    chain = prompt | llm.with_structured_output(_LLMNarrative)
    try:
        narrative: _LLMNarrative = chain.invoke({"facts": json.dumps(facts, indent=2)})
    except (OutputParserException, ValidationError, APIError) as e:
        raise RuntimeError(f"Summarizer LLM call failed: {e}") from e

    next_steps = [l.description for l in (levers or [])]
    return PlanSummary(
        top_line=narrative.top_line,
        retirement_note=narrative.retirement_note,
        goals=narrative.goals,
        cashflow_note=narrative.cashflow_note,
        risks=narrative.risks,
        next_steps=next_steps,
    )
