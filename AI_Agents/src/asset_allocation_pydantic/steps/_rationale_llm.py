from __future__ import annotations

import json
from typing import Any, Dict, List

from pydantic import BaseModel, Field

from common import format_inr_indian

from ..models import (
    AggregatedSubgroupRow,
    BucketAllocation,
    ClientSummary,
    FutureInvestment,
    Goal,
)
from ..tables import LLM_MAX_RETRIES, LLM_MAX_TOKENS, LLM_MODEL_ID


_FALLBACK_RATIONALES: Dict[str, str] = {
    "emergency": (
        "We set aside a safety cushion so an unexpected expense won't force you "
        "to touch the rest of your money."
    ),
    "short_term": (
        "For goals coming up soon, the money stays in steady, predictable "
        "options so it's ready when you need it."
    ),
    "medium_term": (
        "For goals a few years out, we mix steady savings with some growth so "
        "your money keeps up without taking big swings."
    ),
    "long_term": (
        "With many years to go, more of the money can aim for growth since "
        "short-term ups and downs have time to even out."
    ),
}


_INVESTMENT_GOAL_CONTEXT: Dict[str, str] = {
    "retirement": "your retirement nest egg",
    "education": "your child's education",
    "home_purchase": "your home purchase",
    "intergenerational_transfer": "what you'll pass on",
    "wealth_creation": "building long-term wealth",
    "other": "this goal",
}


def _horizon_phrase(months: int) -> str:
    if months < 12:
        return f"{months} month{'s' if months != 1 else ''} away"
    years = months / 12
    if years == int(years):
        y = int(years)
        return f"{y} year{'s' if y != 1 else ''} away"
    return f"about {years:.1f} years away"


def default_goal_rationale(bucket: str, goal: Goal) -> str:
    """Per-goal rationale tailored to bucket, horizon, amount, and goal type."""
    context = _INVESTMENT_GOAL_CONTEXT.get(goal.investment_goal, "this goal")
    horizon = _horizon_phrase(goal.time_to_goal_months)
    amount_str = f"₹{goal.amount_needed:,.0f}"

    if bucket == "short_term":
        return (
            f"For {goal.goal_name} ({context}) — {horizon}, earmarking "
            f"{amount_str} — the money stays in steady, predictable instruments. "
            f"This close to the deadline, protecting the capital matters more "
            f"than chasing returns."
        )
    if bucket == "medium_term":
        return (
            f"For {goal.goal_name} ({context}) — {horizon}, earmarking "
            f"{amount_str} — we split between steady savings and moderate "
            f"growth. The horizon is long enough for markets to help, short "
            f"enough that we don't want to ride big swings."
        )
    if bucket == "long_term":
        return (
            f"For {goal.goal_name} ({context}) — {horizon}, earmarking "
            f"{amount_str} — we lean into growth. Over this horizon compounding "
            f"does the heavy lifting, and short-term dips have plenty of time "
            f"to recover."
        )
    return ""


_SYSTEM_PROMPT = """You write short, plain-language explanations for a personal finance plan.
Rules:
- Use 'you' and 'your'. Talk directly to the person.
- 1 to 3 short sentences per item.
- NO jargon. Forbidden words: alpha, beta, duration, NAV, asset class, volatility, liquidity, corpus, portfolio rebalancing.
- Explain the WHY, not the numbers.
- Money: every rupee field in the input payload has a sibling `_indian` string already formatted in Indian notation (e.g., `amount_needed: 1000000` paired with `amount_needed_indian: "₹10 lakh"`). When you mention a money amount, COPY the matching `_indian` string verbatim. NEVER compute the lakh/crore conversion yourself. NEVER say 'million' or 'billion'.
- Emergency bucket: why the safety cushion and how many months it covers.
- For short_term / medium_term / long_term, write ONE rationale PER goal in the bucket (keyed by that goal's name). Each must reference the specific goal by name, its time horizon, and why the chosen mix fits that horizon and goal type (education, retirement, home, etc.).
- Future investment messages (if any): reframe the gap as 'wealth to create' through ongoing investments over time — NOT as a problem. Do NOT use the words 'shortfall', 'deficit', 'lack', or 'not enough'. Make it clear this view reflects current corpus only, and that closing the gap is doable through some combination of: increasing the savings rate, extending the time horizon, or trimming/deferring negotiable goals. Sound encouraging, concrete, and positive — but do NOT invent monthly investment amounts or SIP figures (we don't have those numbers in the data).
- For ``goal_rationales``, the inner dict for short_term / medium_term / long_term must be keyed by each goal's ``goal_name`` (one entry per goal in that bucket). For ``future_investment_messages``, keys are bucket names (short_term / medium_term / long_term) — emergency does not appear in future-investment messages.
"""


class RationaleResponse(BaseModel):
    bucket_rationales: Dict[str, str] = Field(default_factory=dict)
    future_investment_messages: Dict[str, str] = Field(default_factory=dict)
    goal_rationales: Dict[str, Dict[str, str]] = Field(default_factory=dict)


def _fallback_response(
    bucket_allocations: List[BucketAllocation],
) -> RationaleResponse:
    goal_rationales: Dict[str, Dict[str, str]] = {}
    for b in bucket_allocations:
        if b.bucket == "emergency":
            continue
        per_goal = {g.goal_name: default_goal_rationale(b.bucket, g) for g in b.goals}
        if per_goal:
            goal_rationales[b.bucket] = per_goal
    return RationaleResponse(
        bucket_rationales=dict(_FALLBACK_RATIONALES),
        goal_rationales=goal_rationales,
    )


def no_llm_rationale_fn(
    _client_summary: ClientSummary,
    bucket_allocations: List[BucketAllocation],
    _aggregated: List[AggregatedSubgroupRow],
) -> RationaleResponse:
    """Drop-in rationale_fn that skips the LLM and returns deterministic fallbacks."""
    return _fallback_response(bucket_allocations)


def apply_rationales(
    bucket_allocations: List[BucketAllocation],
    future_investments_summary: List[FutureInvestment],
    rationales: RationaleResponse,
) -> None:
    """Attach bucket and per-goal rationale text, and fill blank future-investment messages."""
    for b in bucket_allocations:
        text = rationales.bucket_rationales.get(b.bucket)
        if text:
            b.rationale = text
        if b.bucket == "emergency":
            continue
        per_goal_from_llm = rationales.goal_rationales.get(b.bucket, {}) or {}
        attached: Dict[str, str] = {}
        for g in b.goals:
            msg = per_goal_from_llm.get(g.goal_name) or default_goal_rationale(b.bucket, g)
            if msg:
                attached[g.goal_name] = msg
        b.goal_rationales = attached

    for fi in future_investments_summary:
        if fi.bucket is None:
            continue
        override = rationales.future_investment_messages.get(fi.bucket)
        if override and not fi.message:
            fi.message = override


def _build_user_payload(
    client_summary: ClientSummary,
    bucket_allocations: List[BucketAllocation],
    aggregated_subgroups: List[AggregatedSubgroupRow],
) -> str:
    """Render the rationale-step LLM payload as JSON.

    Every rupee field is paired with a sibling ``*_indian`` string formatted in
    Indian notation (lakh / crore) by ``format_inr_indian``. The system prompt
    instructs the LLM to copy these strings verbatim instead of converting
    rupee numbers itself — Haiku frequently drops an order of magnitude when
    asked to do the conversion at inference time.
    """
    def _goal_entry(g: Goal) -> Dict[str, Any]:
        return {
            "goal_name": g.goal_name,
            "time_to_goal_months": g.time_to_goal_months,
            "amount_needed": g.amount_needed,
            "amount_needed_indian": format_inr_indian(g.amount_needed),
            "goal_priority": g.goal_priority,
            "investment_goal": g.investment_goal,
        }

    def _future_investment_entry(fi: FutureInvestment | None) -> Dict[str, Any] | None:
        if fi is None:
            return None
        d = fi.model_dump()
        d["future_investment_amount_indian"] = format_inr_indian(fi.future_investment_amount)
        return d

    client_dict = client_summary.model_dump()
    client_dict["total_corpus_indian"] = format_inr_indian(client_summary.total_corpus)

    payload = {
        "client": client_dict,
        "buckets": [
            {
                "bucket": b.bucket,
                "goals": [_goal_entry(g) for g in b.goals],
                "total_goal_amount": b.total_goal_amount,
                "total_goal_amount_indian": format_inr_indian(b.total_goal_amount),
                "allocated_amount": b.allocated_amount,
                "allocated_amount_indian": format_inr_indian(b.allocated_amount),
                "subgroup_amounts": b.subgroup_amounts,
                "future_investment": _future_investment_entry(b.future_investment),
            }
            for b in bucket_allocations
        ],
        "subgroups": [
            {
                "subgroup": r.subgroup,
                "total": r.total,
                "total_indian": format_inr_indian(r.total),
            }
            for r in aggregated_subgroups
        ],
    }
    return json.dumps(payload, default=str)


def generate_rationales(
    client_summary: ClientSummary,
    bucket_allocations: List[BucketAllocation],
    aggregated_subgroups: List[AggregatedSubgroupRow],
) -> RationaleResponse:
    try:
        from langchain_anthropic import ChatAnthropic  # type: ignore
    except Exception:
        return _fallback_response(bucket_allocations)

    client = ChatAnthropic(
        model=LLM_MODEL_ID, max_tokens=LLM_MAX_TOKENS, temperature=0
    ).with_structured_output(RationaleResponse)
    user_payload = _build_user_payload(
        client_summary, bucket_allocations, aggregated_subgroups
    )

    messages = [
        ("system", _SYSTEM_PROMPT),
        ("human", user_payload),
    ]

    for _ in range(LLM_MAX_RETRIES):
        try:
            resp: Any = client.invoke(messages)
            if isinstance(resp, RationaleResponse):
                return resp
        except Exception:
            continue

    return _fallback_response(bucket_allocations)
