"""Run the goal-based allocation pipeline and format results for chat.

Orchestrates: input building, API key resolution, async thread offload,
step-by-step tracing, optional DB persistence, and markdown formatting.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.services.ai_bridge.ailax_trace import trace_line
from app.services.ai_bridge.common import ensure_ai_agents_path

ensure_ai_agents_path()

from goal_based_allocation_pydantic.models import AllocationInput, GoalAllocationOutput
from goal_based_allocation_pydantic.pipeline import run_allocation_with_state
from goal_based_allocation_pydantic.steps._rationale_llm import generate_rationales

from app.services.ai_bridge.goal_allocation_input_builder import (
    build_goal_allocation_input_for_user,
)


def _invoke_pipeline(
    alloc_input: AllocationInput, anthropic_api_key: str,
) -> tuple[dict[str, Any], GoalAllocationOutput]:
    """Run the 7-step pipeline with ``ANTHROPIC_API_KEY`` set for the LLM rationale step."""
    key = anthropic_api_key.strip()
    prev = os.environ.get("ANTHROPIC_API_KEY")
    os.environ["ANTHROPIC_API_KEY"] = key
    try:
        return run_allocation_with_state(alloc_input, rationale_fn=generate_rationales)
    finally:
        if prev is None:
            os.environ.pop("ANTHROPIC_API_KEY", None)
        else:
            os.environ["ANTHROPIC_API_KEY"] = prev

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AllocationRunOutcome:
    """Immutable outcome of one allocation pipeline run."""
    result: GoalAllocationOutput | None
    blocking_message: str | None = None
    rebalancing_recommendation_id: uuid.UUID | None = None
    allocation_snapshot_id: uuid.UUID | None = None


# ---------------------------------------------------------------------------
# Trace / debug helpers
# ---------------------------------------------------------------------------

def _short_json(obj: object, limit: int = 450) -> str:
    """JSON-serialise *obj* and truncate to *limit* chars for trace logs."""
    try:
        s = json.dumps(obj, default=str)
    except TypeError:
        s = str(obj)
    return s[:limit] + "…" if len(s) > limit else s


_STEP_MAP = [
    ("Step 1 (emergency)",    "step1_emergency"),
    ("Step 2 (short-term)",   "step2_short_term"),
    ("Step 3 (medium-term)",  "step3_medium_term"),
    ("Step 4 (long-term)",    "step4_long_term"),
    ("Step 5 (aggregation)",  "step5_aggregation"),
    ("Step 6 (guardrails)",   "step6_guardrails"),
    ("Step 7 (presentation)", "step7_output"),
]


def _summarize_step(label: str, key: str, blob: Any) -> str:
    """One-line summary of a pipeline step for server-side trace logs."""
    try:
        data = blob.model_dump() if hasattr(blob, "model_dump") else blob
    except Exception:
        return f"{label}: {_short_json(blob, 300)}"

    if not isinstance(data, dict):
        return f"{label}: {_short_json(data, 300)}"

    if key == "step1_emergency":
        return (
            f"{label}: emergency={data.get('total_emergency')} "
            f"remaining={data.get('remaining_corpus')}"
        )
    if key in {"step2_short_term", "step3_medium_term"}:
        return (
            f"{label}: goals={len(data.get('goals_allocated', []))} "
            f"allocated={data.get('allocated_amount')} "
            f"remaining={data.get('remaining_corpus')}"
        )
    if key == "step4_long_term":
        return (
            f"{label}: corpus={data.get('total_long_term_corpus')} "
            f"leftover={data.get('leftover_corpus')}"
        )
    if key == "step5_aggregation":
        return (
            f"{label}: rows={len(data.get('rows', []))} "
            f"grand_total={data.get('grand_total')} "
            f"matches={data.get('grand_total_matches_corpus')}"
        )
    if key == "step6_guardrails":
        validation = data.get("validation") or {}
        return (
            f"{label}: all_rules_pass={validation.get('all_rules_pass')} "
            f"fund_mappings={len(data.get('fund_mappings', []))}"
        )
    if key == "step7_output":
        return (
            f"{label}: grand_total={data.get('grand_total')} "
            f"buckets={len(data.get('bucket_allocations', []))}"
        )
    return f"{label}: {_short_json(data, 380)}"


# ---------------------------------------------------------------------------
# Chat formatting
# ---------------------------------------------------------------------------

_BUCKET_ORDER = ["emergency", "short_term", "medium_term", "long_term"]
_BUCKET_TITLES = {
    "emergency": "Emergency",
    "short_term": "Short-term",
    "medium_term": "Medium-term",
    "long_term": "Long-term",
}


def format_allocation_chat_brief(
    output: GoalAllocationOutput, spine_mode: str | None
) -> str:
    """Render a ``GoalAllocationOutput`` as user-facing markdown."""
    cs = output.client_summary
    lines: list[str] = []

    lines.append(
        f"Here is a **goal-based allocation** using your **effective risk score "
        f"{cs.effective_risk_score:.1f}** (age {cs.age}, {len(cs.goals)} goal"
        f"{'s' if len(cs.goals) != 1 else ''}, corpus INR {output.grand_total:,.0f})."
    )
    lines.append("")

    buckets_by_name = {b.bucket: b for b in output.bucket_allocations}
    for bucket_name in _BUCKET_ORDER:
        b = buckets_by_name.get(bucket_name)
        if b is None or b.allocated_amount <= 0:
            continue
        title = _BUCKET_TITLES[bucket_name]
        lines.append(
            f"**{title} — INR {b.allocated_amount:,.0f}** "
            f"(goal need INR {b.total_goal_amount:,.0f})"
        )
        if b.rationale:
            lines.append(f"_{b.rationale}_")
        for g in b.goals:
            rationale = b.goal_rationales.get(g.goal_name)
            bullet = (
                f"- **{g.goal_name}** — INR {g.amount_needed:,.0f}, "
                f"{g.time_to_goal_months} months"
            )
            lines.append(bullet)
            if rationale:
                lines.append(f"  _{rationale}_")
        lines.append("")

    acb = output.asset_class_breakdown
    if acb is not None:
        actual = acb.actual
        lines.append(
            f"**Asset-class mix** — equity {actual.equity_total_pct:.1f}% "
            f"(INR {actual.equity_total:,.0f}), debt {actual.debt_total_pct:.1f}% "
            f"(INR {actual.debt_total:,.0f}), others {actual.others_total_pct:.1f}% "
            f"(INR {actual.others_total:,.0f})."
        )
        lines.append("")

    if output.aggregated_subgroups:
        lines.append("**Allocation mix**")
        for row in output.aggregated_subgroups:
            lines.append(
                f"- {row.customer_label}: INR {row.total:,.0f}"
                + (f" — {row.fund_mapping.recommended_fund}" if row.fund_mapping else "")
            )
        lines.append("")

    if output.future_investments_summary:
        lines.append("**Future investments**")
        for fi in output.future_investments_summary:
            bucket_label = _BUCKET_TITLES.get(fi.bucket or "", fi.bucket or "")
            lines.append(
                f"- {bucket_label}: INR {fi.future_investment_amount:,.0f}"
                + (f" — {fi.message}" if fi.message else "")
            )
        lines.append("")

    if spine_mode != "drift_check":
        lines.append(
            "_Check exit loads and tax before switching schemes; general information only._"
        )

    return "\n".join(lines).rstrip() + "\n"


def _format_allocation_answer_long(
    output: GoalAllocationOutput, user_question: str
) -> str:
    """Longer wrapper used by the standalone allocation HTTP endpoint."""
    return f"Based on your question: {user_question}\n\n{format_allocation_chat_brief(output, 'full')}"


# ---------------------------------------------------------------------------
# Question-tailored chat composer (Haiku)
# ---------------------------------------------------------------------------

_COMPOSER_SYSTEM_PROMPT = (
    "You are Prozpr, an Indian-market financial assistant. The customer's question "
    "routed to the allocation engine, which has produced an authoritative allocation "
    "brief in the user message. Your job: decide whether the customer wants the full "
    "brief, or a tailored answer to a specific question.\n"
    "\n"
    "Decision rules:\n"
    "- 'use_brief_verbatim' — when the customer asked a BROAD allocation request and "
    "would benefit from seeing the full goal-based plan. Examples: 'plan my "
    "portfolio', 'how should I allocate', 'recommend an SIP plan', 'rebalance my "
    "holdings', 'show my allocation strategy'.\n"
    "- 'tailored_answer' — when the customer asked a NARROW question that does NOT "
    "need the full table. Examples: 'is this too risky?', 'why so much equity?', "
    "'how much for retirement?', 'is my emergency fund enough?', 'will this beat "
    "inflation?', 'what's my drift?'.\n"
    "\n"
    "Tailored-answer rules (only when decision='tailored_answer'):\n"
    "- 1 to 4 short sentences. MAXIMUM 80 words.\n"
    "- Use figures from the brief verbatim. NEVER invent rupee amounts, percentages, "
    "goals, or fund names. If the answer requires data not in the brief, say so in "
    "one short line.\n"
    "- Money formatting: lakhs ('L') and crores ('Cr'). Never million/billion.\n"
    "- No preamble, no '**Answer**' heading, no echoing the question, no greeting.\n"
    "- Do not contradict the brief. If the brief says equity 65%, you say equity 65%.\n"
    "- Do not moralize or recommend speaking to an advisor.\n"
    "\n"
    "Response contract: call `return_allocation_reply` exactly once. When "
    "decision='use_brief_verbatim', set 'answer' to an empty string."
)

_COMPOSER_RETURN_TOOL = {
    "name": "return_allocation_reply",
    "description": (
        "Return the final allocation chat reply. Call exactly once. Use "
        "'use_brief_verbatim' for broad allocation requests where the customer wants "
        "the full plan. Use 'tailored_answer' when the customer asked a specific "
        "question that doesn't need the full table."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "decision": {
                "type": "string",
                "enum": ["use_brief_verbatim", "tailored_answer"],
                "description": (
                    "Pick 'use_brief_verbatim' for broad allocation requests; "
                    "'tailored_answer' for narrow questions about the plan."
                ),
            },
            "answer": {
                "type": "string",
                "description": (
                    "When decision='tailored_answer': 1-4 short sentences (≤80 words) "
                    "answering the specific question, drawing only from the brief's "
                    "numbers. When decision='use_brief_verbatim': empty string."
                ),
            },
        },
        "required": ["decision", "answer"],
    },
}


def _extract_allocation_reply(blocks: list[dict]) -> tuple[str, str] | None:
    """Pull (decision, answer) from a `return_allocation_reply` tool_use block."""
    for b in blocks:
        if b.get("type") == "tool_use" and b.get("name") == "return_allocation_reply":
            inp = b.get("input") or {}
            decision = inp.get("decision")
            answer = inp.get("answer", "")
            if (
                decision in ("use_brief_verbatim", "tailored_answer")
                and isinstance(answer, str)
            ):
                return decision, answer
    return None


async def compose_allocation_chat_reply(
    user_question: str,
    deterministic_brief: str,
    mode: str,
) -> str | None:
    """Tailor the allocation chat reply to the customer's specific question.

    Returns the tailored reply when Haiku picks 'tailored_answer'; returns None
    when it picks 'use_brief_verbatim' OR on any failure. The caller falls back
    to the deterministic brief whenever this returns None.
    """
    api_key = get_settings().get_anthropic_key()
    if not api_key:
        return None

    user_prompt = (
        f"Customer question: {user_question}\n\n"
        f"Engine spine mode: {mode}\n\n"
        f"Allocation brief from engine (authoritative — numbers must come ONLY "
        f"from this):\n{deterministic_brief}"
    )

    payload = {
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 600,
        "system": _COMPOSER_SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": user_prompt}],
        "tools": [_COMPOSER_RETURN_TOOL],
        "tool_choice": {"type": "tool", "name": "return_allocation_reply"},
    }
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers=headers,
                json=payload,
            )
        if resp.status_code != 200:
            logger.warning(
                "Allocation composer returned status %d; falling back to deterministic brief",
                resp.status_code,
            )
            return None
        parsed = _extract_allocation_reply(resp.json().get("content", []))
        if parsed is None:
            logger.warning("Allocation composer returned no parseable tool call; falling back")
            return None
        decision, answer = parsed
        if decision == "use_brief_verbatim":
            return None
        return answer.strip() or None
    except Exception:
        logger.exception("Allocation composer failed; falling back to deterministic brief")
        return None


# ---------------------------------------------------------------------------
# Blocking-message helpers
# ---------------------------------------------------------------------------

_MSG_MISSING_DOB = (
    "I need your date of birth to build a personalised allocation — it anchors "
    "your risk profile and time horizon. Head to your profile, add it, then "
    "ask me again."
)

_MSG_NO_API_KEY = (
    "Sorry, I can't run the allocation engine right now. Please try again in a "
    "few minutes. If the issue persists, let us know via the help option and "
    "we'll get it sorted."
)

_MSG_ENGINE_ERROR = (
    "Something went wrong while calculating your allocation. Please try again "
    "in a moment. If it keeps happening, reach out via the help option and "
    "we'll take a look."
)


# ---------------------------------------------------------------------------
# Core pipeline orchestration
# ---------------------------------------------------------------------------

async def compute_allocation_result(
    user,
    user_question: str,
    *,
    db: AsyncSession | None = None,
    persist_recommendation: bool = False,
    acting_user_id: uuid.UUID | None = None,
    chat_session_id: uuid.UUID | None = None,
    spine_mode: str | None = None,
) -> AllocationRunOutcome:
    """Build inputs, run the 7-step pipeline, optionally persist, and return."""
    del user_question  # reserved for future carve-out hints from chat
    trace_line("module: asset_allocation — building inputs")

    if getattr(user, "date_of_birth", None) is None:
        return AllocationRunOutcome(result=None, blocking_message=_MSG_MISSING_DOB)

    try:
        alloc_input, build_debug = build_goal_allocation_input_for_user(user)
    except ValueError:
        return AllocationRunOutcome(result=None, blocking_message=_MSG_MISSING_DOB)

    trace_line(
        f"effective_risk_score={alloc_input.effective_risk_score} "
        f"(willingness={alloc_input.risk_willingness}, capacity={alloc_input.risk_capacity_score})"
    )
    trace_line(
        f"allocation input: age={alloc_input.age}, corpus={alloc_input.total_corpus}, "
        f"goals={len(alloc_input.goals)}"
    )

    api_key = get_settings().get_anthropic_asset_allocation_key()
    if not api_key:
        return AllocationRunOutcome(result=None, blocking_message=_MSG_NO_API_KEY)

    try:
        full_state, output = await asyncio.to_thread(
            _invoke_pipeline, alloc_input, api_key,
        )
    except Exception as exc:
        logger.exception("goal_based_allocation pipeline failed: %s", exc)
        trace_line(f"goal_based_allocation ERROR: {exc!s}")
        return AllocationRunOutcome(result=None, blocking_message=_MSG_ENGINE_ERROR)

    for label, key in _STEP_MAP:
        trace_line(
            _summarize_step(label, key, full_state[key]) if key in full_state
            else f"{label}: <missing in state>"
        )
    trace_line(f"GoalAllocationOutput grand_total={output.grand_total}")
    trace_line(f"input builder debug: {_short_json(build_debug, 600)}")

    reb_id: uuid.UUID | None = None
    snap_id: uuid.UUID | None = None
    if db is not None and persist_recommendation and output is not None and acting_user_id is not None:
        from app.services.allocation_recommendation_persist import (
            persist_goal_allocation_recommendation,
        )

        reb_id, snap_id = await persist_goal_allocation_recommendation(
            db, acting_user_id, output,
            chat_session_id=chat_session_id,
            user_question=None,
            spine_mode=spine_mode,
        )
        trace_line(f"persisted: rebalancing_id={reb_id} snapshot_id={snap_id}")

    return AllocationRunOutcome(
        result=output,
        blocking_message=None,
        rebalancing_recommendation_id=reb_id,
        allocation_snapshot_id=snap_id,
    )


# ---------------------------------------------------------------------------
# Standalone HTTP entry point
# ---------------------------------------------------------------------------

async def generate_portfolio_optimisation_response(
    user,
    user_question: str,
    *,
    db: AsyncSession | None = None,
    persist_recommendation: bool = False,
    acting_user_id: uuid.UUID | None = None,
) -> str:
    """Run allocation for standalone HTTP and return a full user-facing string."""
    outcome = await compute_allocation_result(
        user, user_question,
        db=db,
        persist_recommendation=persist_recommendation,
        acting_user_id=acting_user_id,
        spine_mode="api_asset_allocation",
    )
    if outcome.blocking_message:
        return outcome.blocking_message
    if outcome.result:
        return _format_allocation_answer_long(outcome.result, user_question)
    return (
        "I could not produce an allocation result.\n\n"
        "**Justification**\n"
        "- The allocation engine returned no structured output."
    )
