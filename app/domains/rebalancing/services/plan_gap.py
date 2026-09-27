"""Why a rebalancing plan's target stops short of the customer's GOAL mix.

Pure and deterministic — no DB, no LLM, no I/O.

The Invest page used to show two bars, Current and Target, where "Target" means
*where this plan lands*. Customers who had set an explicit preference (say 50/50
equity/debt) read that bar as their preference and reported it broken: the plan
landed at 77/20 and nothing on the page said why. Both numbers were right; the
page just never showed the third one, nor the constraint between them.

THE CONSTRAINT, in one line: a rebalance is cash-neutral and short-term money is
not for sale. Optional trims are long-term-only — the engine realises STCG only on
a force-exit (`Rebalancing/steps/step4_initial_trades_under_stcg_cap.py`) — so the
whole plan is funded by whatever has crossed a year. A portfolio that is 84%
under a year old can move 16% of itself, however far off the goal mix it sits.
``rebalancing_fund_rows.pass2_undersell_amount`` is the engine's own record of the
rupees it wanted to sell and could not, which is the number this module quotes.

SHAPE: a ``question`` the page shows as a single amber line, and a ``summary`` +
``points`` + ``footnote`` it reveals on tap. Split that way because the constraint
takes a paragraph to state honestly and a paragraph parked under a chart goes
unread — the question carries the fact (the two percentages) so the collapsed line
is already an answer, and the expansion carries the why. Copy lives HERE, not in
the component, so chat and the page cannot describe one plan two ways.

Consumed by the run-detail router through ``RebalancingAssetClassBreakdown.gap``.
Inputs are duck-typed, so the tests use plain stand-ins.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from app.domains.ai_engine.common import format_inr_indian

# Below this many percentage points the plan is close enough that calling out a
# gap would be noise — a customer does not need a disclosure about 2 points.
GAP_MIN_PCT = 3

# Customer-facing names. "Others" is gold-dominated and is called Commodity on
# the preferences screen and the chart legend; keep the three surfaces in step.
_CLASS_WORD = {"Equity": "equity", "Debt": "debt", "Others": "commodity"}

# Tie-break order for "which class is furthest off" — see build_plan_gap.
_CLASS_ORDER = ("Equity", "Debt", "Others")

# The mechanic, stated once. True of every rebalance, locked money or not, and the
# thing customers do not know: there is no fresh cash in a rebalance, so the buy
# budget IS the sell proceeds.
_CASH_NEUTRAL_POINT = (
    "A rebalance adds no new money. Every rupee it buys comes from something it sells."
)

_FOOTNOTE = (
    "A SIP or a lump sum is invested at your goal mix directly, with nothing to "
    "sell — that's the quicker way to close the rest of the gap."
)


@dataclass(frozen=True)
class PlanGap:
    """The amber disclosure under the Current-vs-Target bars."""

    question: str  # the one collapsed line; carries both percentages
    summary: str  # the answer in one plain sentence
    points: list[str]  # short specifics, the rupee figures
    footnote: str | None


def _pct_of_total(mix: dict[str, float]) -> dict[str, float]:
    total = sum(mix.values())
    if total <= 0:
        return {}
    return {cls: amount / total * 100.0 for cls, amount in mix.items()}


def short_term_locked_inr(fund_rows: Iterable[Any]) -> float:
    """Rupees the plan wanted to sell but could not, because they are short-term.

    ``pass2_undersell_amount`` is the FINAL shortfall — step 5 has already spent any
    carryforward-loss headroom on force-exits, so what remains is money the
    short-term-gains rule keeps off the table.
    """
    return float(
        sum(float(getattr(row, "pass2_undersell_amount", 0) or 0) for row in fund_rows)
    )


def build_plan_gap(
    goal_mix: dict[str, float],
    target_mix: dict[str, float],
    *,
    locked_inr: float,
    moved_inr: float,
) -> PlanGap | None:
    """The disclosure for one run, or ``None`` when the plan lands on plan.

    ``goal_mix`` / ``target_mix`` are title-case Equity/Debt/Others → ₹ (either
    rollup from ``asset_class_breakdown``); each is converted to a share of its own
    total, so the two are comparable even though a plan's net cash flow shifts the
    absolute totals slightly. ``moved_inr`` is the plan's gross sell value — what it
    was actually able to shift.
    """
    goal_pct = _pct_of_total(goal_mix)
    target_pct = _pct_of_total(target_mix)
    if not goal_pct or not target_pct:
        return None

    # Canonical order, not set order: on a two-class portfolio the gaps are always
    # equal and opposite (goal 70/30 vs plan 40/60 is 30 points either way), so the
    # tie has to break the same way every time or one plan gets two sentences across
    # two server processes. Both statements are true; only the arbitrariness is a bug.
    worst = max(
        (cls for cls in _CLASS_ORDER if cls in goal_pct or cls in target_pct),
        key=lambda cls: abs(goal_pct.get(cls, 0.0) - target_pct.get(cls, 0.0)),
    )
    goal_v = round(goal_pct.get(worst, 0.0))
    target_v = round(target_pct.get(worst, 0.0))
    if abs(goal_v - target_v) < GAP_MIN_PCT:
        return None

    word = _CLASS_WORD.get(worst, worst.lower())
    points = [_CASH_NEUTRAL_POINT]
    if locked_inr > 0:
        summary = "Not all of your money is free to move yet."
        points.append(
            f"{format_inr_indian(locked_inr)} of your funds are under a year old. "
            "Selling those would mean short-term capital gains tax, so this plan "
            "leaves them alone."
        )
        if moved_inr > 0:
            points.append(
                f"{format_inr_indian(moved_inr)} has crossed a year. That's all "
                "this plan can move today."
            )
    else:
        summary = "A rebalance can only shuffle the funds you already own."
        points.append(
            "Per-fund caps and lock-ins stop it from going the whole way in one step."
        )

    return PlanGap(
        question=f"This plan reaches {target_v}% {word}, not your {goal_v}%. Why?",
        summary=summary,
        points=points,
        footnote=_FOOTNOTE,
    )


def goal_target_total_inr(subgroup_summaries: Sequence[Any]) -> float:
    """Total ₹ the goal mix allocates — 0 when the run has no subgroup summaries."""
    return float(
        sum(float(getattr(s, "goal_target_inr", 0) or 0) for s in subgroup_summaries)
    )
