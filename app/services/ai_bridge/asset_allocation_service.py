"""AI bridge — `asset_allocation_service.py`.

Sits between FastAPI services/routers and the ``AI_Agents/src`` packages (added to `sys.path` via ``ensure_ai_agents_path``). Handles env keys, async/thread boundaries, and user-context mapping. Ideal mutual fund allocation is invoked from here using ``Ideal_asset_allocation`` inside the app layer (e.g. ``ideal_allocation_runner``) so `AI_Agents` files stay untouched.
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


@dataclass(frozen=True)
class AllocationRunOutcome:
    """Result of running the Ideal_asset_allocation pipeline once."""

    result: AllocationOutput | None
    blocking_message: str | None = None
    rebalancing_recommendation_id: uuid.UUID | None = None
    allocation_snapshot_id: uuid.UUID | None = None


def _short_json(obj: object, limit: int = 450) -> str:
    try:
        s = json.dumps(obj, default=str)
    except TypeError:
        s = str(obj)
    if len(s) > limit:
        return s[:limit] + "…"
    return s


def _summarize_step(step_label: str, key: str, blob: object) -> str:
    if not isinstance(blob, dict):
        return f"{step_label}: {_short_json(blob)}"
    out = blob.get("output", blob)
    if key == "step1_carve_outs" and isinstance(out, dict):
        rem = out.get("remaining_investable_corpus")
        carve = (
            out.get("carve_outs")
            or out.get("carve_out_allocations")
            or out.get("locked_in_carve_outs")
            or []
        )
        n = len(carve) if isinstance(carve, list) else 0
        return f"{step_label}: remaining_investable_corpus={rem}; carve_out_lines≈{n}"
    if key == "step2_asset_class" and isinstance(out, dict):
        pick = {k: out.get(k) for k in ("equities_pct", "debt_pct", "others_pct") if k in out}
        return f"{step_label}: {_short_json(pick) if pick else _short_json(out, 300)}"
    if key == "step3_subgroups" and isinstance(out, dict):
        return f"{step_label}: output keys={list(out.keys())[:10]}"
    if key == "step4_validation" and isinstance(out, dict):
        flag = out.get("all_rules_pass")
        if flag is not None:
            return f"{step_label}: all_rules_pass={flag}"
        return f"{step_label}: {_short_json(out, 380)}"
    if key == "step5_presentation" and isinstance(out, dict):
        gt = out.get("grand_total")
        cs = out.get("client_summary")
        ers = cs.get("effective_risk_score") if isinstance(cs, dict) else None
        return f"{step_label}: grand_total={gt}; effective_risk_score={ers}"
    return f"{step_label}: {_short_json(out, 380)}"


def format_allocation_chat_brief(allocation_result: AllocationOutput, spine_mode: str) -> str:
    """Short user-facing text for chat (Ideal_asset_allocation final JSON)."""
    cs = allocation_result.client_summary
    ac = allocation_result.asset_class_allocation
    lines: list[str] = []

    lines.append(
        f"Here is an **ideal allocation** view using your **effective risk score {cs.effective_risk_score:.1f}** "
        f"(age {cs.age}, horizon: {cs.investment_horizon}, goal: {cs.investment_goal})."
    )
    lines.append("")
    lines.append("**Target mix (class level)**")
    lines.append(
        f"- Equities: **{ac.equities.pct}%** (~INR {ac.equities.amount:,.0f})"
    )
    lines.append(f"- Debt: **{ac.debt.pct}%** (~INR {ac.debt.amount:,.0f})")
    lines.append(f"- Others: **{ac.others.pct}%** (~INR {ac.others.amount:,.0f})")
    lines.append("")
    lines.append("**Subgroup highlights**")
    sg = allocation_result.subgroup_allocation
    any_sg = False
    for bucket_name, items in (
        ("Equity", sg.equity[:4]),
        ("Debt", sg.debt[:3]),
        ("Others", sg.others[:2]),
    ):
        for it in items:
            any_sg = True
            lines.append(
                f"- {bucket_name}: **{it.subgroup}** ~{it.pct}% "
                f"(~INR {it.amount:,.0f}) — {it.recommended_fund}"
            )
    if not any_sg:
        lines.append("- (No subgroup rows in model output.)")
    lines.append("")
    lines.append(
        f"_Carve-outs total **INR {allocation_result.total_carve_outs:,.0f}**; "
        f"remaining investable **INR {allocation_result.remaining_investable_corpus:,.0f}**; "
        f"grand total **INR {allocation_result.grand_total:,.0f}**._"
    )
    if spine_mode != "drift_check":
        lines.append("")
        lines.append(
            "_Check exit loads and tax before switching schemes; general information only._"
        )
    return "\n".join(lines)


def _format_allocation_answer_long(allocation_result: AllocationOutput, user_question: str) -> str:
    """Longer format for standalone allocation API."""
    head = format_allocation_chat_brief(allocation_result, spine_mode="full")
    return f"Based on your question: {user_question}\n\n{head}"


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
    trace_line("module: asset_allocation (Ideal_asset_allocation) — building inputs")
    if getattr(user, "date_of_birth", None) is None:
        return AllocationRunOutcome(
            result=None,
            blocking_message=(
                "I can optimise your portfolio, but your date of birth is missing. "
                "Please complete your profile first.\n\n"
                "**Justification**\n"
                "- Age is required for risk scoring and allocation inputs."
            ),
        )

    try:
        alloc_input, risk_debug = build_allocation_input_for_user(user, user_question)
    except ValueError:
        return AllocationRunOutcome(
            result=None,
            blocking_message=(
                "I can optimise your portfolio, but your date of birth is missing. "
                "Please complete your profile first.\n\n"
                "**Justification**\n"
                "- Age is required for risk scoring and allocation inputs."
            ),
        )

    trace_line(
        f"effective_risk_score (risk_profiling.scoring)={alloc_input.effective_risk_score} "
        f"(willingness={alloc_input.risk_willingness}, capacity={alloc_input.risk_capacity_score})"
    )
    trace_line(f"allocation input summary: age={alloc_input.age}, total_corpus={alloc_input.total_corpus}, "
               f"horizon={alloc_input.investment_horizon!r}")

    api_key = get_settings().get_anthropic_asset_allocation_key()
    if not api_key:
        return AllocationRunOutcome(
            result=None,
            blocking_message=(
                "I can review your portfolio direction, but the allocation engine is temporarily unavailable.\n\n"
                "**Answer**\n"
                "- Keep broad diversification across equity, debt, and gold based on your risk profile.\n"
                "- Prioritize rebalancing if any sleeve drifts materially from your target.\n\n"
                "**Justification**\n"
                "- Set `ASSET_ALLOCATION_API_KEY` in `.env` (or `PORTFOLIO_QUERY_API_KEY` / `ANTHROPIC_API_KEY`).\n"
            ),
        )

    def _run_chain():
        return invoke_ideal_allocation_with_full_state(alloc_input, api_key)

    try:
        full_state, output = await asyncio.to_thread(_run_chain)
    except Exception as exc:
        logger.exception("Ideal_asset_allocation pipeline failed: %s", exc)
        trace_line(f"Ideal_asset_allocation ERROR: {exc!s}")
        return AllocationRunOutcome(
            result=None,
            blocking_message=(
                "I could not run the allocation engine right now. Please try again shortly.\n\n"
                "**Justification**\n"
                "- The Ideal_asset_allocation module raised an error; see server logs for detail."
            ),
        )

    trace_line("Ideal_asset_allocation — step outputs (short):")
    step_map = [
        ("Step 1 (carve-outs)", "step1_carve_outs"),
        ("Step 2 (asset class)", "step2_asset_class"),
        ("Step 3 (subgroups)", "step3_subgroups"),
        ("Step 4 (validation)", "step4_validation"),
        ("Step 5 (presentation)", "step5_presentation"),
    ]
    for label, k in step_map:
        if k in full_state:
            trace_line(_summarize_step(label, k, full_state[k]))
        else:
            trace_line(f"{label}: <missing in state>")

    trace_line(
        f"returning from app.services.ai_bridge.asset_allocation_service "
        f"→ AllocationOutput grand_total={output.grand_total}"
    )
    trace_line(f"risk scoring payload (debug): {_short_json(risk_debug, 800)}")

    reb_id: uuid.UUID | None = None
    snap_id: uuid.UUID | None = None
    if (
        db is not None
        and persist_recommendation
        and output is not None
        and acting_user_id is not None
    ):
        from app.services.allocation_recommendation_persist import (
            persist_ideal_allocation_recommendation,
        )

        reb_id, snap_id = await persist_ideal_allocation_recommendation(
            db,
            acting_user_id,
            output,
            chat_session_id=chat_session_id,
            user_question=user_question,
            spine_mode=spine_mode,
        )
        trace_line(
            f"persisted ideal allocation plan rebalancing_id={reb_id} snapshot_id={snap_id}"
        )

    return AllocationRunOutcome(
        result=output,
        blocking_message=None,
        rebalancing_recommendation_id=reb_id,
        allocation_snapshot_id=snap_id,
    )


async def generate_portfolio_optimisation_response(
    user,
    user_question: str,
    *,
    db: AsyncSession | None = None,
    persist_recommendation: bool = False,
    acting_user_id: uuid.UUID | None = None,
) -> str:
    """Run Ideal_asset_allocation for standalone HTTP and return a full user-facing string."""
    outcome = await compute_allocation_result(
        user,
        user_question,
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
