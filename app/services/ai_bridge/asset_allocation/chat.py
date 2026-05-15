"""Chat handler for the ``asset_allocation`` intent — runs engine and formats tables."""

from __future__ import annotations

import logging
from typing import Any

from app.services.ai_bridge.asset_allocation.service import (
    MSG_ALLOCATION_MISSING_DOB,
    MSG_ALLOCATION_UPGRADING,
    compute_allocation_result,
)
from app.services.ai_bridge.chat_dispatcher import ChatHandlerResult, register
from app.services.ai_bridge.common import ensure_ai_agents_path, format_inr_indian as _format_inr_raw, trace_line
from app.services.chat_core.turn_context import TurnContext

logger = logging.getLogger(__name__)

ensure_ai_agents_path()


def _fmt_inr(amount: float) -> str:
    """format_inr_indian wrapper that never returns None."""
    return _format_inr_raw(amount) or "₹0"


def _format_aa_tables(output: Any) -> str:
    """Build markdown tables from GoalAllocationOutput for chat display."""
    lines: list[str] = []

    # --- Client Summary ---
    cs = getattr(output, "client_summary", None)
    if cs:
        lines.append("## Client Summary")
        lines.append("")
        lines.append("| Parameter | Value |")
        lines.append("|---|---|")
        lines.append(f"| Age | {getattr(cs, 'age', '-')} |")
        lines.append(f"| Effective Risk Score | {getattr(cs, 'effective_risk_score', '-')} |")
        lines.append(f"| Total Corpus | {_fmt_inr(float(getattr(cs, 'total_corpus', 0)))} |")
        goals = getattr(cs, "goals", []) or []
        lines.append(f"| Number of Goals | {len(goals)} |")
        lines.append("")

    # --- Asset Class Breakdown ---
    acb = getattr(output, "asset_class_breakdown", None)
    if acb:
        actual = getattr(acb, "actual", None)
        if actual:
            lines.append("## Asset Class Allocation")
            lines.append("")
            lines.append("| Asset Class | Amount (₹) | Percentage |")
            lines.append("|---|---|---|")
            eq_total = float(getattr(actual, "equity_total", 0) or 0)
            debt_total = float(getattr(actual, "debt_total", 0) or 0)
            others_total = float(getattr(actual, "others_total", 0) or 0)
            eq_pct = float(getattr(actual, "equity_total_pct", 0) or 0)
            debt_pct = float(getattr(actual, "debt_total_pct", 0) or 0)
            others_pct = float(getattr(actual, "others_total_pct", 0) or 0)
            lines.append(f"| **Equity** | {_fmt_inr(eq_total)} | {eq_pct:.1f}% |")
            lines.append(f"| **Debt** | {_fmt_inr(debt_total)} | {debt_pct:.1f}% |")
            lines.append(f"| **Others** | {_fmt_inr(others_total)} | {others_pct:.1f}% |")
            grand = eq_total + debt_total + others_total
            lines.append(f"| **Total** | {_fmt_inr(grand)} | 100% |")
            lines.append("")

        # Per-bucket breakdown
        per_bucket = getattr(actual, "per_bucket", None) if actual else None
        if per_bucket:
            lines.append("### Per-Bucket Asset Class Split")
            lines.append("")
            lines.append("| Bucket | Equity (₹) | Debt (₹) | Others (₹) | Equity % | Debt % | Others % |")
            lines.append("|---|---|---|---|---|---|---|")
            for b in per_bucket:
                bname = getattr(b, "bucket", "-").replace("_", " ").title()
                lines.append(
                    f"| {bname} "
                    f"| {_fmt_inr(float(getattr(b, 'equity', 0)))} "
                    f"| {_fmt_inr(float(getattr(b, 'debt', 0)))} "
                    f"| {_fmt_inr(float(getattr(b, 'others', 0)))} "
                    f"| {float(getattr(b, 'equity_pct', 0)):.1f}% "
                    f"| {float(getattr(b, 'debt_pct', 0)):.1f}% "
                    f"| {float(getattr(b, 'others_pct', 0)):.1f}% |"
                )
            lines.append("")

    # --- Bucket Allocations ---
    buckets = getattr(output, "bucket_allocations", []) or []
    if buckets:
        lines.append("## Bucket Allocations")
        lines.append("")
        lines.append("| Bucket | Goals | Total Goal Amount | Allocated Amount |")
        lines.append("|---|---|---|---|")
        for ba in buckets:
            bname = getattr(ba, "bucket", "-").replace("_", " ").title()
            goals = getattr(ba, "goals", []) or []
            goal_names = ", ".join(getattr(g, "goal_name", "") for g in goals) or "-"
            total_goal = float(getattr(ba, "total_goal_amount", 0) or 0)
            allocated = float(getattr(ba, "allocated_amount", 0) or 0)
            lines.append(
                f"| {bname} | {goal_names} "
                f"| {_fmt_inr(total_goal)} "
                f"| {_fmt_inr(allocated)} |"
            )
        lines.append("")

    # --- Aggregated Subgroups ---
    agg = getattr(output, "aggregated_subgroups", []) or []
    if agg:
        lines.append("## Subgroup Allocation (Aggregated)")
        lines.append("")
        lines.append("| Subgroup | Emergency | Short Term | Medium Term | Long Term | **Total** |")
        lines.append("|---|---|---|---|---|---|")
        for row in agg:
            sg = getattr(row, "subgroup", "-").replace("_", " ").title()
            emg = _fmt_inr(float(getattr(row, "emergency", 0) or 0))
            st = _fmt_inr(float(getattr(row, "short_term", 0) or 0))
            mt = _fmt_inr(float(getattr(row, "medium_term", 0) or 0))
            lt = _fmt_inr(float(getattr(row, "long_term", 0) or 0))
            total = _fmt_inr(float(getattr(row, "total", 0) or 0))
            lines.append(f"| {sg} | {emg} | {st} | {mt} | {lt} | **{total}** |")
        lines.append("")

    # --- Grand Total ---
    grand_total = float(getattr(output, "grand_total", 0) or 0)
    lines.append(f"**Grand Total Allocated:** {_fmt_inr(grand_total)}")
    lines.append("")

    # --- Future Investments ---
    fi = getattr(output, "future_investments_summary", []) or []
    fi_with_amount = [f for f in fi if float(getattr(f, "future_investment_amount", 0) or 0) > 0]
    if fi_with_amount:
        lines.append("## Future Investments Needed")
        lines.append("")
        lines.append("| Bucket | Amount | Note |")
        lines.append("|---|---|---|")
        for f in fi_with_amount:
            bname = (getattr(f, "bucket", "-") or "-").replace("_", " ").title()
            amt = _fmt_inr(float(getattr(f, "future_investment_amount", 0) or 0))
            msg = getattr(f, "message", "-") or "-"
            lines.append(f"| {bname} | {amt} | {msg} |")
        lines.append("")

    return "\n".join(lines)


@register("asset_allocation")
async def handle(ctx: TurnContext) -> ChatHandlerResult:
    """Run the asset allocation engine and return formatted tables."""
    trace_line("asset_allocation_chat: running engine")

    if getattr(ctx.user_ctx, "date_of_birth", None) is None:
        return ChatHandlerResult(text=MSG_ALLOCATION_MISSING_DOB)

    outcome = await compute_allocation_result(
        ctx.user_ctx,
        ctx.user_question,
        db=ctx.db,
        persist_recommendation=True,
        acting_user_id=ctx.effective_user_id,
        chat_session_id=ctx.session_id,
        spine_mode="asset_allocation_chat",
    )

    if outcome.blocking_message:
        return ChatHandlerResult(text=outcome.blocking_message)

    if outcome.result is None:
        return ChatHandlerResult(text=MSG_ALLOCATION_UPGRADING)

    # Format the engine output as markdown tables
    tables_md = _format_aa_tables(outcome.result)

    header = "Here's your personalised asset allocation:\n\n"
    text = header + tables_md

    # NOTE: Stopping here — NOT running rebalancing or any subsequent module.
    # This is intentional for testing the asset allocation response in isolation.

    return ChatHandlerResult(
        text=text,
        asset_allocation_run_id=outcome.asset_allocation_run_id,
    )
