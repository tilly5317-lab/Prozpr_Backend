"""Sectioned markdown output for the rebalancing chat reply.

Voice: financially-savvy friend, not advisor. Plain language, no compliance
boilerplate, contractions OK. All copy lives in this file so tone iterations
don't touch the structured data path.
"""

from __future__ import annotations

from decimal import Decimal

from app.services.ai_bridge.common import ensure_ai_agents_path

ensure_ai_agents_path()

from Rebalancing.models import (  # type: ignore[import-not-found]  # noqa: E402
    RebalancingComputeResponse,
    RebalancingWarning,
    SubgroupSummary,
    WarningCode,
)


_LEAD_REFRESHED = (
    "_First I redid your asset mix from your goals, then worked out the trades to get there._"
)
_CLOSING = (
    "_Worth a sanity check on exit loads and tax before you pull the trigger._"
)


def _fmt_inr(amount: Decimal | float | int) -> str:
    return f"₹{Decimal(amount):,.0f}"


def _warning_line(w: RebalancingWarning) -> str:
    code = w.code
    if code == WarningCode.BAD_FUND_DETECTED:
        funds = ", ".join(w.affected_isins) or "a few funds"
        return (
            f"- {funds} aren't on the recommended list anymore — "
            "worth exiting when the tax math works."
        )
    if code == WarningCode.UNREBALANCED_REMAINDER:
        return (
            f"- {w.message} couldn't be placed cleanly under the per-fund caps — "
            "small enough to ignore."
        )
    if code == WarningCode.STCG_BUDGET_BINDING:
        return (
            "- Held back some sells to keep short-term gains under your offset budget."
        )
    if code == WarningCode.NO_HOLDINGS_FOR_RECOMMENDED_FUND:
        funds = ", ".join(w.affected_isins) or "a recommended fund"
        return f"- {funds} on your plan but you don't hold it yet — fresh purchase."
    return f"- {w.message}"


def _subgroup_block(s: SubgroupSummary) -> list[str]:
    lines: list[str] = []
    lines.append(
        f"**{s.asset_subgroup}** — you'd land at {_fmt_inr(s.suggested_final_holding_inr)} "
        f"(target was {_fmt_inr(s.goal_target_inr)})."
    )
    for row in s.actions:
        if row.pass1_buy_amount and row.pass1_buy_amount > 0:
            lines.append(
                f"- Put {_fmt_inr(row.pass1_buy_amount)} into {row.recommended_fund}."
            )
        sell_total = (row.pass1_sell_amount or Decimal(0)) + (
            row.pass2_sell_amount or Decimal(0)
        )
        if sell_total > 0:
            verb = "Pull" if not row.exit_flag else "Exit"
            lines.append(
                f"- {verb} {_fmt_inr(sell_total)} out of {row.recommended_fund}."
            )
    return lines


def format_rebalancing_chat_brief(
    response: RebalancingComputeResponse,
    *,
    used_cached_allocation: bool,
) -> str:
    """Render the engine response as a chat-ready markdown brief."""
    out: list[str] = []

    if not used_cached_allocation:
        out.append(_LEAD_REFRESHED)
        out.append("")

    totals = response.totals
    n_trades = (
        totals.funds_to_buy_count
        + totals.funds_to_sell_count
        + totals.funds_to_exit_count
    )
    corpus = response.metadata.request_corpus_inr
    out.append(
        f"Here's how I'd rebalance — {n_trades} moves on a corpus of about {_fmt_inr(corpus)}."
    )
    out.append("")

    for s in response.subgroups:
        out.extend(_subgroup_block(s))
        out.append("")

    if (totals.total_tax_estimate_inr or 0) > 0 or (
        totals.total_exit_load_inr or 0
    ) > 0:
        out.append(
            f"The trade-offs: about {_fmt_inr(totals.total_tax_estimate_inr)} in taxes and "
            f"{_fmt_inr(totals.total_exit_load_inr)} in exit loads, with "
            f"{_fmt_inr(totals.total_stcg_realised)} short-term and "
            f"{_fmt_inr(totals.total_ltcg_realised)} long-term gains realised."
        )
        out.append("")

    if response.warnings:
        out.append("**A couple of heads-ups:**")
        for w in response.warnings:
            out.append(_warning_line(w))
        out.append("")

    out.append(_CLOSING)
    return "\n".join(out).rstrip() + "\n"
