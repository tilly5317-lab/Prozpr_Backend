"""goal_waterfall: held short-term money first, then the front-loaded SIP, then corpus."""

from __future__ import annotations

import random

import pytest

from asset_allocation_pydantic.models import Goal
from asset_allocation_pydantic.steps.step2_short_term import goal_waterfall

L = 100_000
CORPUS = 100 * L


def _g(name, months, amount, fv=None):
    return Goal(goal_name=name, time_to_goal_months=months, amount_needed=amount,
                goal_priority="non_negotiable", amount_needed_fv=fv)


FOUR_GOALS = [_g("G1", 6, 3 * L), _g("G2", 10, 4 * L), _g("G3", 14, 2 * L), _g("G4", 20, 6 * L)]


def _run(goals, holdings, sip, corpus=CORPUS, subgroup="short_debt"):
    return goal_waterfall(goals, holdings, sip, corpus, subgroup)


def test_holdings_cover_near_goals_and_full_sip_builds_the_last():
    f = _run(FOUR_GOALS, 9 * L, 50_000)
    assert f.allocated_amount == 9 * L
    assert f.from_corpus == 0
    assert f.shortfall == 0
    assert f.monthly_sip_to_goals == 50_000
    rows = {r.goal_name: r for r in f.goals}
    assert [rows[n].from_holdings for n in ("G1", "G2", "G3")] == [3 * L, 4 * L, 2 * L]
    assert rows["G4"].from_sip == 6 * L
    assert rows["G4"].from_corpus == 0


def test_small_sip_leaves_a_gap_the_corpus_covers_now():
    f = _run(FOUR_GOALS, 9 * L, 20_000)
    assert f.from_corpus == 2 * L
    assert f.allocated_amount == 11 * L
    assert f.monthly_sip_to_goals == 20_000
    g4 = next(r for r in f.goals if r.goal_name == "G4")
    assert (g4.from_sip, g4.from_corpus, g4.shortfall) == (4 * L, 2 * L, 0)


def test_last_window_splits_the_sip():
    assert _run([_g("Car", 20, 5 * L)], 0.0, 50_000).monthly_sip_to_goals == 50_000
    assert _run([_g("Car", 14, 5 * L)], 3 * L, 50_000).monthly_sip_to_goals == 33_334


def test_goal_inside_the_window_is_met_on_time():
    f = _run([_g("Fees", 2, 1 * L)], 0.0, 50_000)
    assert f.from_corpus == 0
    assert f.monthly_sip_to_goals == 50_000


def test_corpus_too_small_reports_shortfall_and_full_sip():
    f = _run([_g("House", 12, 10 * L)], 0.0, 10_000, corpus=3 * L)
    assert f.allocated_amount == 3 * L
    assert f.shortfall == 580_000
    assert f.monthly_sip_to_goals == 10_000


def test_no_holdings_on_file_sends_the_whole_sip_to_goals():
    f = _run([_g("Car", 20, 6 * L)], None, 20_000, corpus=50 * L)
    assert f.allocated_amount == 2 * L
    assert f.from_corpus == 2 * L
    assert f.monthly_sip_to_goals == 20_000
    car = f.goals[0]
    assert (car.from_sip, car.from_corpus, car.shortfall) == (4 * L, 2 * L, 0)


def test_holdings_beyond_every_goal_send_the_sip_long_term():
    f = _run([_g("Trip", 10, 5 * L)], 20 * L, 50_000)
    assert f.allocated_amount == 5 * L
    assert f.monthly_sip_to_goals == 0


def test_no_sip_carves_the_gap_after_holdings():
    f = _run([_g("A", 6, 3 * L), _g("B", 12, 2 * L)], 1 * L, 0.0)
    assert f.allocated_amount == 5 * L
    assert f.from_corpus == 4 * L
    assert f.monthly_sip_to_goals == 0


def test_no_goals():
    f = _run([], 5 * L, 50_000)
    assert f.allocated_amount == 0
    assert f.monthly_sip_to_goals == 0
    assert f.goals == []


def test_goal_share_never_exceeds_an_odd_sip():
    assert _run([_g("Car", 20, 10 * L)], 0.0, 12_345).monthly_sip_to_goals == 12_345
    assert _run([_g("Gift", 20, 20_000)], 0.0, 12_345).monthly_sip_to_goals == 3_334


def test_future_value_is_used_when_given():
    f = _run([_g("Car", 12, 5 * L, fv=6 * L)], 0.0, 0.0)
    assert f.allocated_amount == 6 * L
    assert f.goals[0].amount_needed_fv == 6 * L


def test_attribution_invariants_hold_on_random_cases():
    rng = random.Random(20260928)
    for _ in range(2000):
        goals = [_g(f"g{i}", rng.randint(1, 35), rng.randint(1, 5000) * 1000)
                 for i in range(rng.randint(1, 5))]
        holdings = float(rng.randint(0, 3000) * 1000)
        sip = float(rng.randint(0, 200) * 500)
        f = goal_waterfall(goals, holdings, sip, 10**12, "arbitrage")
        for r in f.goals:
            parts = r.from_holdings + r.from_sip + r.from_corpus + r.shortfall
            assert parts == pytest.approx(r.amount_needed_fv)
        left, cum, need = holdings, 0.0, 0.0
        for g in sorted(goals, key=lambda g: g.time_to_goal_months):
            used = min(left, g.amount_needed)
            left -= used
            cum += g.amount_needed - used
            need = max(need, cum - sip * g.time_to_goal_months)
        assert sum(r.from_corpus + r.shortfall for r in f.goals) == pytest.approx(need)
        assert 0 <= f.monthly_sip_to_goals <= sip
