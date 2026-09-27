"""Tests for the amber "why isn't this my preference?" disclosure.

The bug this exists for: a customer saved a 50/50 equity/debt preference, the
allocation engine honoured it exactly (goal mix 50.1/49.9), and the Invest page's
Target bar read 77/20 because 84% of their holdings were under a year old and a
rebalance cannot sell short-term units. Both numbers were right; the page showed
only one of them and never named the constraint.
"""

import pytest

from app.domains.rebalancing.services.plan_gap import (
    GAP_MIN_PCT,
    build_plan_gap,
    goal_target_total_inr,
    short_term_locked_inr,
)


def _fund_row(undersell):
    return type("FR", (), {"pass2_undersell_amount": undersell})()


def _sg(goal_target):
    return type("SG", (), {"goal_target_inr": goal_target})()


# The real run: goal 50/50/0, plan lands 77/20/3, ₹55,804 locked, ₹13,118 moved.
REAL_GOAL = {"Equity": 41000.0, "Debt": 40900.0, "Others": 0.0}
REAL_TARGET = {"Equity": 63076.0, "Debt": 16016.0, "Others": 2635.0}


# --------------------------------------------------------------------------
# The locked-money figure
# --------------------------------------------------------------------------


def test_locked_is_the_sum_of_final_undersells():
    rows = [_fund_row(29454.95), _fund_row(26349.52), _fund_row(0)]
    assert short_term_locked_inr(rows) == pytest.approx(55804.47)


def test_locked_tolerates_none_and_no_rows():
    assert short_term_locked_inr([]) == 0.0
    assert short_term_locked_inr([_fund_row(None)]) == 0.0


def test_goal_total_sums_subgroup_goals():
    assert goal_target_total_inr([_sg(41000.0), _sg(40900.0)]) == pytest.approx(81900.0)
    assert goal_target_total_inr([]) == 0.0


# --------------------------------------------------------------------------
# When the disclosure appears at all
# --------------------------------------------------------------------------


def test_nothing_when_the_plan_reaches_the_goal():
    mix = {"Equity": 500.0, "Debt": 500.0}
    assert build_plan_gap(mix, dict(mix), locked_inr=0.0, moved_inr=0.0) is None


def test_nothing_for_a_gap_under_the_threshold():
    # 1 point off — a disclosure about it would be noise.
    goal = {"Equity": 500.0, "Debt": 500.0}
    target = {"Equity": 510.0, "Debt": 490.0}
    assert build_plan_gap(goal, target, locked_inr=100.0, moved_inr=50.0) is None


def test_nothing_when_either_mix_is_empty():
    mix = {"Equity": 100.0}
    assert build_plan_gap({}, mix, locked_inr=0.0, moved_inr=0.0) is None
    assert build_plan_gap(mix, {}, locked_inr=0.0, moved_inr=0.0) is None
    # All-zero rupees is the same case — no basis to compare shares on.
    assert build_plan_gap({"Equity": 0.0}, mix, locked_inr=0.0, moved_inr=0.0) is None


def test_mixes_are_compared_as_shares_not_rupees():
    # A rebalance's net cash flow shifts the absolute totals a little; that must
    # not read as a gap. Same 50/50 shares, different totals.
    goal = {"Equity": 500.0, "Debt": 500.0}
    target = {"Equity": 5000.0, "Debt": 5000.0}
    assert build_plan_gap(goal, target, locked_inr=999.0, moved_inr=1.0) is None


def test_threshold_constant_is_the_one_knob():
    goal = {"Equity": 500.0, "Debt": 500.0}
    just_under = 50 + GAP_MIN_PCT - 1
    target = {"Equity": float(just_under), "Debt": float(100 - just_under)}
    assert build_plan_gap(goal, target, locked_inr=0.0, moved_inr=0.0) is None
    just_over = 50 + GAP_MIN_PCT
    target = {"Equity": float(just_over), "Debt": float(100 - just_over)}
    assert build_plan_gap(goal, target, locked_inr=0.0, moved_inr=0.0) is not None


# --------------------------------------------------------------------------
# The collapsed line — it must answer "what" on its own
# --------------------------------------------------------------------------


def test_question_carries_both_percentages_and_the_worst_class():
    gap = build_plan_gap(REAL_GOAL, REAL_TARGET, locked_inr=55804.0, moved_inr=13118.0)
    assert gap is not None
    # Debt is 30 points off, equity 27 — debt wins.
    assert gap.question == "This plan reaches 20% debt, not your 50%. Why?"


def test_question_names_whichever_class_is_furthest_off():
    # Equity is 40 points off; debt 25, commodity 15.
    goal = {"Equity": 70000.0, "Debt": 30000.0, "Others": 0.0}
    target = {"Equity": 30000.0, "Debt": 55000.0, "Others": 15000.0}
    gap = build_plan_gap(goal, target, locked_inr=0.0, moved_inr=1.0)
    assert gap is not None and "equity" in gap.question


def test_a_tie_breaks_the_same_way_every_time():
    # With two classes the gaps are ALWAYS equal and opposite, so which one gets
    # named is a tie. Set iteration order made it vary between server processes;
    # the canonical Equity/Debt/Others order pins it.
    goal = {"Equity": 70000.0, "Debt": 30000.0}
    target = {"Equity": 40000.0, "Debt": 60000.0}
    questions = {
        build_plan_gap(goal, target, locked_inr=0.0, moved_inr=1.0).question
        for _ in range(20)
    }
    assert questions == {"This plan reaches 40% equity, not your 70%. Why?"}


def test_others_is_called_commodity():
    # Matches the preferences screen and the chart legend; "Others" is internal.
    goal = {"Equity": 500.0, "Debt": 500.0, "Others": 0.0}
    target = {"Equity": 450.0, "Debt": 150.0, "Others": 400.0}
    gap = build_plan_gap(goal, target, locked_inr=0.0, moved_inr=0.0)
    assert gap is not None and "commodity" in gap.question


# --------------------------------------------------------------------------
# The expanded detail
# --------------------------------------------------------------------------


def test_detail_leads_with_the_cash_neutral_mechanic():
    # The thing customers do not know, and it is true of every rebalance — so it
    # is point one on both branches.
    for locked in (55804.0, 0.0):
        gap = build_plan_gap(REAL_GOAL, REAL_TARGET, locked_inr=locked, moved_inr=13118.0)
        assert gap is not None
        assert "adds no new money" in gap.points[0]


def test_detail_quotes_the_locked_and_movable_rupees():
    gap = build_plan_gap(REAL_GOAL, REAL_TARGET, locked_inr=55804.0, moved_inr=13118.0)
    assert gap is not None
    body = " ".join(gap.points)
    assert "55,804" in body and "under a year old" in body
    assert "13,118" in body and "crossed a year" in body
    assert "short-term capital gains" in body
    assert gap.summary == "Not all of your money is free to move yet."


def test_detail_drops_the_movable_line_when_the_plan_moved_nothing():
    gap = build_plan_gap(REAL_GOAL, REAL_TARGET, locked_inr=55804.0, moved_inr=0.0)
    assert gap is not None
    body = " ".join(gap.points)
    assert "55,804" in body
    assert "crossed a year" not in body


def test_detail_switches_branch_when_nothing_is_locked():
    gap = build_plan_gap(
        {"Equity": 200.0, "Debt": 800.0},
        {"Equity": 800.0, "Debt": 200.0},
        locked_inr=0.0,
        moved_inr=400.0,
    )
    assert gap is not None
    body = " ".join(gap.points)
    assert "short-term capital gains" not in body
    assert "caps and lock-ins" in body
    assert gap.summary == "A rebalance can only shuffle the funds you already own."


def test_footnote_always_points_at_the_unconstrained_route():
    # New money has neither the cash-neutral nor the short-term constraint, which
    # is why SIP and lump sum hit the goal mix and a rebalance cannot.
    for locked, moved in ((55804.0, 13118.0), (0.0, 400.0)):
        gap = build_plan_gap(REAL_GOAL, REAL_TARGET, locked_inr=locked, moved_inr=moved)
        assert gap is not None and gap.footnote is not None
        assert "SIP" in gap.footnote and "lump sum" in gap.footnote


def test_copy_carries_no_emoji():
    # House rule: lucide icons, never emoji in customer copy.
    gap = build_plan_gap(REAL_GOAL, REAL_TARGET, locked_inr=55804.0, moved_inr=13118.0)
    assert gap is not None
    text = " ".join([gap.question, gap.summary, *gap.points, gap.footnote or ""])
    assert all(ord(ch) < 0x2190 or ch in "—₹" for ch in text), text
