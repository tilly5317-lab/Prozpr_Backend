"""Run the Ideal_asset_allocation pipeline and format results for chat.

Orchestrates: input building, API key resolution, async thread offload,
step-by-step tracing, optional DB persistence, and markdown formatting.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.services.ai_bridge.ailax_trace import trace_line
from app.services.ai_bridge.common import ensure_ai_agents_path

ensure_ai_agents_path()

from Ideal_asset_allocation.models import AllocationOutput

from app.services.ai_bridge.effective_risk_from_user import build_allocation_input_for_user
from app.services.ai_bridge.ideal_allocation_runner import invoke_ideal_allocation_with_full_state

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AllocationRunOutcome:
    """Immutable outcome of one allocation pipeline run."""
    result: AllocationOutput | None
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


# Map of (label, state-key) for the 5-step pipeline trace.
_STEP_MAP = [
    ("Step 1 (carve-outs)",  "step1_carve_outs"),
    ("Step 2 (asset class)", "step2_asset_class"),
    ("Step 3 (subgroups)",   "step3_subgroups"),
    ("Step 4 (validation)",  "step4_validation"),
    ("Step 5 (presentation)","step5_presentation"),
]


def _summarize_step(label: str, key: str, blob: object) -> str:
    """One-line summary of a pipeline step for server-side trace logs."""
    if not isinstance(blob, dict):
        return f"{label}: {_short_json(blob)}"

    out = blob.get("output", blob)

    if key == "step1_carve_outs" and isinstance(out, dict):
        carve = out.get("carve_outs") or out.get("carve_out_allocations") or out.get("locked_in_carve_outs") or []
        return f"{label}: remaining_investable_corpus={out.get('remaining_investable_corpus')}; carve_out_lines≈{len(carve) if isinstance(carve, list) else 0}"

    if key == "step2_asset_class" and isinstance(out, dict):
        pick = {k: out[k] for k in ("equities_pct", "debt_pct", "others_pct") if k in out}
        return f"{label}: {_short_json(pick) if pick else _short_json(out, 300)}"

    if key == "step3_subgroups" and isinstance(out, dict):
        return f"{label}: output keys={list(out.keys())[:10]}"

    if key == "step4_validation" and isinstance(out, dict):
        flag = out.get("all_rules_pass")
        return f"{label}: all_rules_pass={flag}" if flag is not None else f"{label}: {_short_json(out, 380)}"

    if key == "step5_presentation" and isinstance(out, dict):
        cs = out.get("client_summary")
        ers = cs.get("effective_risk_score") if isinstance(cs, dict) else None
        return f"{label}: grand_total={out.get('grand_total')}; effective_risk_score={ers}"

    return f"{label}: {_short_json(out, 380)}"


# ---------------------------------------------------------------------------
# Chat formatting
# ---------------------------------------------------------------------------

def _sum_subgroup_amounts(sg) -> float:
    """Total INR across all subgroup rows."""
    total = 0.0
    for items in (sg.equity, sg.debt, sg.others):
        for it in items:
            total += float(it.amount or 0.0)
    return total


def format_allocation_chat_brief(allocation_result: AllocationOutput, spine_mode: str) -> str:
    """Convert AllocationOutput into user-facing markdown for the chat bubble."""
    cs = allocation_result.client_summary
    ac = allocation_result.asset_class_allocation
    gt = allocation_result.grand_total or 1.0
    ric = allocation_result.remaining_investable_corpus or 1.0
    tco = allocation_result.total_carve_outs or 0.0
    lines: list[str] = []

    lines.append(
        f"Here is an **ideal allocation** view using your **effective risk score "
        f"{cs.effective_risk_score:.1f}** (age {cs.age}, horizon: {cs.investment_horizon}, "
        f"goal: {cs.investment_goal})."
    )
    lines.append("")

    # Class-level: show % of grand total from amounts (matches model INR; sums to ~100%).
    lines.append("**Target mix (class level)** _(each line is % of grand total)_")
    lines.append(
        f"- Equities: **{(ac.equities.amount / gt) * 100:.2f}%** (INR {ac.equities.amount:,.2f})"
    )
    lines.append(f"- Debt: **{(ac.debt.amount / gt) * 100:.2f}%** (INR {ac.debt.amount:,.2f})")
    lines.append(f"- Others: **{(ac.others.amount / gt) * 100:.2f}%** (INR {ac.others.amount:,.2f})")
    lines.append("")

    # Carve-outs: % of grand total (carve + investable should equal grand total).
    if allocation_result.carve_outs:
        lines.append("**Carve-outs** _(each line is % of grand total)_")
        for co in allocation_result.carve_outs:
            co_pct = (co.amount / gt) * 100
            lines.append(f"- {co.type}: {co_pct:.2f}% (INR {co.amount:,.2f}) — {co.fund_type}")
        lines.append("")

    # Fund rows: % of *remaining investable* so lines sum to 100% (subgroup sums often
    # round short of R; we surface the gap as Unallocated).
    sg = allocation_result.subgroup_allocation
    fund_sum = _sum_subgroup_amounts(sg)
    lines.append("**Fund-level allocation** _(each line is % of remaining investable corpus)_")
    any_sg = False
    for bucket, items in (("Equity", sg.equity), ("Debt", sg.debt), ("Others", sg.others)):
        for it in items:
            any_sg = True
            pct_inv = (it.amount / ric) * 100 if ric else 0.0
            pct_gross = (it.amount / gt) * 100
            lines.append(
                f"- {bucket}: **{it.subgroup}** {pct_inv:.2f}% of investable "
                f"({pct_gross:.2f}% of grand total) "
                f"(INR {it.amount:,.2f}) — {it.recommended_fund}"
            )
    unallocated = ric - fund_sum
    if not any_sg:
        lines.append("- (No subgroup rows in model output.)")
    elif abs(unallocated) > 0.01:
        lines.append(
            f"- **Unallocated** (rounding / model gap): "
            f"{(unallocated / ric) * 100:.2f}% of investable "
            f"({(unallocated / gt) * 100:.2f}% of grand total) "
            f"(INR {unallocated:,.2f})"
        )

    lines.append("")
    carve_pct = (tco / gt) * 100
    inv_pct = (ric / gt) * 100
    lines.append(
        f"_Grand total **INR {gt:,.2f}** = carve-outs **{carve_pct:.2f}%** "
        f"(INR {tco:,.2f}) + investable **{inv_pct:.2f}%** (INR {ric:,.2f}). "
        f"Subgroup funds sum to INR {fund_sum:,.2f}; fund-level % of investable sum to **100.00%**._"
    )
    if spine_mode != "drift_check":
        lines.append("")
        lines.append("_Check exit loads and tax before switching schemes; general information only._")

    return "\n".join(lines)


def _format_allocation_answer_long(allocation_result: AllocationOutput, user_question: str) -> str:
    """Longer wrapper used by the standalone allocation HTTP endpoint."""
    return f"Based on your question: {user_question}\n\n{format_allocation_chat_brief(allocation_result, 'full')}"


# ---------------------------------------------------------------------------
# Blocking-message helpers (avoid repeating long strings inline)
# ---------------------------------------------------------------------------

_MSG_MISSING_DOB = (
    "I can optimise your portfolio, but your date of birth is missing. "
    "Please complete your profile first.\n\n"
    "**Justification**\n"
    "- Age is required for risk scoring and allocation inputs."
)

_MSG_NO_API_KEY = (
    "I can review your portfolio direction, but the allocation engine is temporarily unavailable.\n\n"
    "**Answer**\n"
    "- Keep broad diversification across equity, debt, and gold based on your risk profile.\n"
    "- Prioritize rebalancing if any sleeve drifts materially from your target.\n\n"
    "**Justification**\n"
    "- Set `ASSET_ALLOCATION_API_KEY` in `.env` (or `PORTFOLIO_QUERY_API_KEY` / `ANTHROPIC_API_KEY`).\n"
)

_MSG_ENGINE_ERROR = (
    "I could not run the allocation engine right now. Please try again shortly.\n\n"
    "**Justification**\n"
    "- The Ideal_asset_allocation module raised an error; see server logs for detail."
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
    """Build inputs, run the 5-step allocation chain, optionally persist, and return."""
    trace_line("module: asset_allocation — building inputs")

    # Guard: date of birth is required for risk scoring.
    if getattr(user, "date_of_birth", None) is None:
        return AllocationRunOutcome(result=None, blocking_message=_MSG_MISSING_DOB)

    try:
        alloc_input, risk_debug = build_allocation_input_for_user(user, user_question)
    except ValueError:
        return AllocationRunOutcome(result=None, blocking_message=_MSG_MISSING_DOB)

    trace_line(
        f"effective_risk_score={alloc_input.effective_risk_score} "
        f"(willingness={alloc_input.risk_willingness}, capacity={alloc_input.risk_capacity_score})"
    )
    trace_line(
        f"allocation input: age={alloc_input.age}, corpus={alloc_input.total_corpus}, "
        f"horizon={alloc_input.investment_horizon!r}"
    )

    api_key = get_settings().get_anthropic_asset_allocation_key()
    if not api_key:
        return AllocationRunOutcome(result=None, blocking_message=_MSG_NO_API_KEY)

    # Run the blocking LCEL chain off the event loop.
    try:
        full_state, output = await asyncio.to_thread(
            invoke_ideal_allocation_with_full_state, alloc_input, api_key,
        )
    except Exception as exc:
        logger.exception("Ideal_asset_allocation pipeline failed: %s", exc)
        trace_line(f"Ideal_asset_allocation ERROR: {exc!s}")
        return AllocationRunOutcome(result=None, blocking_message=_MSG_ENGINE_ERROR)

    # Trace each pipeline step.
    for label, key in _STEP_MAP:
        trace_line(
            _summarize_step(label, key, full_state[key]) if key in full_state
            else f"{label}: <missing in state>"
        )
    trace_line(f"AllocationOutput grand_total={output.grand_total}")
    trace_line(f"risk scoring debug: {_short_json(risk_debug, 800)}")

    # Optionally persist the recommendation for the /execute page.
    reb_id: uuid.UUID | None = None
    snap_id: uuid.UUID | None = None
    if db is not None and persist_recommendation and output is not None and acting_user_id is not None:
        from app.services.allocation_recommendation_persist import persist_ideal_allocation_recommendation

        reb_id, snap_id = await persist_ideal_allocation_recommendation(
            db, acting_user_id, output,
            chat_session_id=chat_session_id,
            user_question=user_question,
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
