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
        lines.append("**Subgroups**")
        for row in output.aggregated_subgroups:
            lines.append(
                f"- {row.subgroup}: INR {row.total:,.0f}"
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
# Blocking-message helpers
# ---------------------------------------------------------------------------

_MSG_MISSING_DOB = (
    "I can optimise your portfolio, but your date of birth is missing. "
    "Please complete your profile first.\n\n"
    "**Justification**\n"
    "- Age is required for allocation inputs."
)

_MSG_NO_API_KEY = (
    "I can review your portfolio direction, but the allocation engine is temporarily unavailable.\n\n"
    "**Answer**\n"
    "- Keep broad diversification across equity, debt, and gold based on your risk profile.\n"
    "- Prioritize rebalancing if any sleeve drifts materially from your target.\n\n"
    "**Justification**\n"
    "- The allocation pipeline needs a valid Anthropic key. Set `ASSET_ALLOCATION_API_KEY` or "
    "`ANTHROPIC_API_KEY` in `.env` (see console.anthropic.com), then **fully restart** uvicorn so "
    "cached settings reload.\n"
)

_MSG_ENGINE_ERROR = (
    "I could not run the allocation engine right now. Please try again shortly.\n\n"
    "**Justification**\n"
    "- The goal_based_allocation_pydantic pipeline raised an error; see server logs for detail."
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
