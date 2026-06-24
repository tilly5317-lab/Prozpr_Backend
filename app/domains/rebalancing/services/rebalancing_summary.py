"""Build a plan-aware headline (title + subtitle) for a rebalancing run.

Pure and deterministic — no DB, no LLM. Reads the run's roll-up ``totals`` plus
per-asset-class direction (``subgroup_summaries[*].rebalance_inr``) and picks the
ONE dominant story to lead with, so the Invest page can replace its static
"Time to fine-tune your mix." header with copy that reflects the actual plan.

Consumed by ``RebalancingRunDetailResponse.summary`` (a computed field). The
inputs are duck-typed: anything exposing the documented attributes works, which
keeps the function trivially unit-testable with plain stand-ins.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from app.domains.ai_engine.common import format_inr_indian


@dataclass(frozen=True)
class RebalanceSummary:
    title: str
    subtitle: str
    reason: str | None = None  # one-line "why", or None when nothing needs justifying


def _class_phrase(asset_class: str) -> str:
    """Lower-case, sentence-friendly form (e.g. 'equity', 'other assets')."""
    return "other assets" if asset_class == "Others" else asset_class.lower()


def _class_title(asset_class: str) -> str:
    """Title-position form (e.g. 'Equity', 'other assets')."""
    return "other assets" if asset_class == "Others" else asset_class


def _exit_reason(trades: Sequence) -> str:
    """Why these funds need replacing — the consequence, tied to the goals they
    fund. Picked from the dominant exit reason_code across trades."""
    codes = [(getattr(t, "reason_code", "") or "") for t in trades]
    low = codes.count("exit_low_rated")
    bad = codes.count("exit_bad_fund")
    if low and low >= bad:
        return "They're dragging on the returns your goals depend on."
    if bad:
        return "They're no longer recommended and could hold your goals back."
    return "They no longer meet our quality bar and could weigh on your goals."


def _drift_reason(asset_class: str, is_trim: bool) -> str:
    """The consequence of leaving the drift in place, tied back to the customer's
    goals — what's at stake, not a restatement of the over/under-weight fact."""
    if is_trim:
        return {
            "Equity": "That's more market risk than your goals call for — a downturn now would set them back.",
            "Debt": "You're more defensive than your goals need, which can hold back their growth.",
        }.get(
            asset_class,
            "These have grown past plan, pulling weight from the assets your goals rely on.",
        )
    return {
        "Equity": "Without it, your long-term goals may fall short on growth.",
        "Debt": "Your cushion has thinned — leaving your nearer-term goals exposed if markets dip.",
    }.get(
        asset_class,
        "Topping these up restores the diversification your goals are built on.",
    )


def _flows_by_class(subgroups: Sequence) -> dict[str, dict[str, float]]:
    """Per asset class: ``net`` (signed change = final − current), the GROSS
    ``buy`` / ``sell`` transacted, and the ``cur`` / ``final`` rupee holdings (now
    vs where THIS PLAN lands). We deliberately use suggested_final_holding_inr —
    the plan's actual destination — NOT goal_target_inr (the unconstrained goal
    ideal), so the card agrees with the trades when constraints push the plan away
    from the ideal. Net decides the dominant class + verb; cur/final give the
    weightage we quote; buy/sell are a rupee fallback."""
    flows: dict[str, dict[str, float]] = {}
    for s in subgroups:
        f = flows.setdefault(
            s.asset_class, {"net": 0.0, "buy": 0.0, "sell": 0.0, "cur": 0.0, "final": 0.0}
        )
        f["net"] += float(s.rebalance_inr or 0)
        f["buy"] += float(s.total_buy_inr or 0)
        f["sell"] += float(s.total_sell_inr or 0)
        f["cur"] += float(s.current_holding_inr or 0)
        f["final"] += float(s.suggested_final_holding_inr or 0)
    return flows


def _flows_from_breakdown(breakdown) -> dict[str, dict[str, float]]:
    """Per-class flows derived from the multi-asset-aware asset-class breakdown the
    Invest bars render — so the headline's weightage swing matches the bars exactly.

    The breakdown already splits blended (multi-asset / hybrid) funds across
    Equity/Debt/Others via the look-through; rolling subgroups up here instead
    would lump them into one class and quote a wrong (much smaller) swing.
    """
    flows: dict[str, dict[str, float]] = {}
    for row in breakdown.rows:
        cur = float(row.current_inr or 0)
        final = float(row.target_inr or 0)
        net = final - cur
        flows[row.asset_class] = {
            "net": net,
            "buy": max(0.0, net),
            "sell": max(0.0, -net),
            "cur": cur,
            "final": final,
        }
    return flows


def build_rebalance_summary(
    totals, subgroups=(), trades=(), breakdown=None
) -> RebalanceSummary | None:
    """Return the headline for a run, or ``None`` when there are no totals.

    When the look-through ``breakdown`` is supplied (the same Equity/Debt/Others
    split the Invest bars show), the weightage swing is quoted from it so the
    headline and the bars agree; otherwise we fall back to the raw subgroup rollup.
    """
    if totals is None:
        return None

    buy_n = totals.funds_to_buy_count or 0
    sell_n = totals.funds_to_sell_count or 0
    exit_n = totals.funds_to_exit_count or 0
    n_trades = buy_n + sell_n + exit_n

    if n_trades == 0:
        return RebalanceSummary(
            title="You're on target",
            subtitle="Your mix already matches your plan — nothing to change today.",
        )

    tax = float(totals.total_tax_estimate_inr or 0)
    tax_clause = f", with tax kept to {format_inr_indian(tax)}" if tax > 0 else ""

    # 1 — Replacing funds dominates the story whenever any fund is fully exited.
    if exit_n > 0:
        noun = "fund" if exit_n == 1 else "funds"
        return RebalanceSummary(
            title=f"Replacing {exit_n} underperforming {noun}",
            subtitle=f"Swapping into recommended picks{tax_clause}.",
            reason=_exit_reason(trades),
        )

    flows = (
        _flows_from_breakdown(breakdown)
        if breakdown is not None and getattr(breakdown, "rows", None)
        else _flows_by_class(subgroups)
    )

    # No per-class direction to lean on (e.g. summaries unavailable) — keep a
    # generic two-way header.
    if not flows or all(f["net"] == 0 for f in flows.values()):
        return RebalanceSummary(
            title="Fine-tuning your mix",
            subtitle=(
                f"{n_trades} trades to glide your portfolio back to your target "
                f"mix{tax_clause}."
            ),
            reason="Your mix has drifted from what your goals call for.",
        )

    # 2 — Lead with the single most off-target asset class. The biggest net move
    # tells the story even when total buys and sells roughly cancel out (the usual
    # rebalance), so we deliberately do NOT gate on net cash-flow dominance —
    # otherwise nearly every plan collapses to the generic header above. The
    # amount we QUOTE is the gross buy/sell, not the net drift.
    dominant = max(flows, key=lambda c: abs(flows[c]["net"]))
    f = flows[dominant]
    phrase = _class_phrase(dominant)

    # Quote the weightage swing (how many allocation points the plan moves this
    # class) — the same number the drift bars show. Falls back to a rupee amount
    # when holdings aren't available, or when the swing rounds below 1 point.
    total_cur = sum(fl["cur"] for fl in flows.values())
    total_final = sum(fl["final"] for fl in flows.values())
    pct = None
    if total_cur > 0 and total_final > 0:
        points = round(abs(f["cur"] / total_cur * 100 - f["final"] / total_final * 100))
        pct = points if points >= 1 else None

    if f["net"] > 0:
        if pct is not None:
            change = f"Raising your {phrase} weightage by {pct}% to reach your target mix."
        else:
            amount = f["buy"] or abs(f["net"])
            change = f"Putting {format_inr_indian(amount)} to work in {phrase} to reach your target mix."
        return RebalanceSummary(
            title=f"Topping up your {_class_title(dominant)}",
            subtitle=change,
            reason=_drift_reason(dominant, is_trim=False),
        )

    tax_paren = f" ({format_inr_indian(tax)} est.)" if tax > 0 else ""
    if pct is not None:
        change = (
            f"Reducing your {phrase} weightage by {pct}% — "
            f"Prozpr picked the lowest-tax units{tax_paren}."
        )
    else:
        amount = f["sell"] or abs(f["net"])
        change = (
            f"Selling {format_inr_indian(amount)} of {phrase} — "
            f"Prozpr picked the lowest-tax units{tax_paren}."
        )
    return RebalanceSummary(
        title=f"Trimming your {_class_title(dominant)} back to target",
        subtitle=change,
        reason=_drift_reason(dominant, is_trim=True),
    )
