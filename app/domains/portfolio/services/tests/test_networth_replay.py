"""Every business rule in the net-worth replay, as a table.

``replay.py`` is pure on purpose — no DB, no clock, no I/O — so each of these is a
plain function call with hand-written numbers. Most of them encode a specific
production bug; the docstrings say which, because a test whose reason is lost gets
"simplified" away by the next person.

Run: ``python -m pytest app/domains/portfolio/services/tests/test_networth_replay.py``
(this directory is gitignored, so a new file here needs ``git add -f``).
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal as D

import pytest

from app.domains.portfolio.services.networth.replay import (
    MAX_GAIN_PCT,
    MAX_HISTORY_YEARS,
    MAX_MONEY,
    Opening,
    Txn,
    clamp_money,
    combine,
    gain_pct,
    replay_scheme,
    window_start,
)

TODAY = date(2026, 1, 10)

# A simple rising NAV curve with a deliberate gap between Jan 5 and Jan 10.
NAVS = [
    (date(2026, 1, 1), D("10")),
    (date(2026, 1, 5), D("12")),
    (date(2026, 1, 10), D("15")),
]


def buy(day: date, units: str, nav: str, amount: str) -> Txn:
    return Txn(day, "BUY", D(units), D(nav), D(amount))


def sell(day: date, units: str, nav: str, amount: str) -> Txn:
    """CAS prints redemption units as a NEGATIVE number — mirror that here."""
    return Txn(day, "SELL", D(f"-{units}"), D(nav), D(amount))


# ── Unit signs ───────────────────────────────────────────────────────────────


def test_negative_redemption_units_reduce_the_balance():
    """The Rs 1.33cr-vs-Rs 1.07cr bug: ``units -= (-x)`` ADDS the units back."""
    series = replay_scheme(
        [buy(date(2026, 1, 1), "100", "10", "1000"), sell(date(2026, 1, 5), "40", "12", "480")],
        NAVS,
        TODAY,
    )
    assert series.end_units == D("60")
    assert series.end_value == D("900")  # 60 units x Rs 15


def test_full_redemption_is_worth_nothing_and_costs_nothing():
    """A closed position must read 0/0/0 — not "invested Rs 1000, worth Rs 0, -100%"."""
    series = replay_scheme(
        [buy(date(2026, 1, 1), "100", "10", "1000"), sell(date(2026, 1, 5), "100", "12", "1200")],
        NAVS,
        TODAY,
    )
    rows, _ = combine({"x": series}, TODAY)
    assert (rows[-1].total_value, rows[-1].total_invested, rows[-1].gain_percentage) == (
        D("0.00"),
        D("0.00"),
        D("0.0000"),
    )


def test_over_redemption_clamps_at_zero_and_is_counted():
    """A partial ledger can redeem units it never saw bought."""
    series = replay_scheme([sell(date(2026, 1, 5), "50", "12", "600")], NAVS, TODAY)
    assert series.end_units == D("0")
    assert series.warnings["negative_units_clamped"] == 1


# ── Cost basis ───────────────────────────────────────────────────────────────


def test_profitable_sell_never_drives_invested_negative():
    """Average-cost method: reduce basis by the COST of units sold, not the proceeds."""
    series = replay_scheme(
        [buy(date(2026, 1, 1), "100", "10", "1000"), sell(date(2026, 1, 5), "90", "50", "4500")],
        NAVS,
        TODAY,
    )
    assert series.end_cost == D("100")  # 10 of 100 units remain, at Rs 10 cost each


def test_partial_sells_keep_basis_proportional():
    series = replay_scheme(
        [
            buy(date(2026, 1, 1), "100", "10", "1000"),
            sell(date(2026, 1, 2), "25", "11", "275"),
            sell(date(2026, 1, 3), "25", "11", "275"),
        ],
        NAVS,
        TODAY,
    )
    assert series.end_units == D("50")
    assert series.end_cost == D("500")


# ── Pricing ──────────────────────────────────────────────────────────────────


def test_held_position_with_no_nav_history_is_priced_off_its_stated_nav():
    """107 of 850 held schemes had no published NAV at their first transaction.

    Valuing them at zero showed a total loss the user never took.
    """
    series = replay_scheme([buy(date(2026, 1, 1), "10", "25", "250")], [], TODAY)
    assert series.end_value == D("250")
    assert series.degraded is True
    assert series.warnings["priced_off_statement"] == 1


def test_published_nav_always_beats_the_stated_one():
    series = replay_scheme([buy(date(2026, 1, 1), "10", "25", "250")], NAVS, TODAY)
    assert series.end_value == D("150")  # 10 x Rs 15 published, not 10 x Rs 25 stated
    assert series.degraded is False


def test_nav_is_carried_forward_across_gaps():
    """Weekends, holidays and publication gaps still need a well-defined value."""
    series = replay_scheme(
        [buy(date(2026, 1, 1), "10", "10", "100")],
        [(date(2026, 1, 1), D("10")), (date(2026, 1, 9), D("20"))],
        TODAY,
    )
    assert series.value_by_day[date(2026, 1, 5)] == D("100")  # carried
    assert series.value_by_day[date(2026, 1, 10)] == D("200")  # carried past Jan 9


def test_a_long_stale_price_is_flagged_degraded():
    """A fund that stopped publishing is still valued — but no longer presented as current."""
    series = replay_scheme(
        [buy(date(2025, 1, 1), "10", "10", "100")],
        [(date(2025, 1, 1), D("10"))],
        TODAY,
    )
    assert series.end_value == D("100")
    assert series.degraded is True
    assert series.warnings["stale_nav"] == 1


# ── Window ───────────────────────────────────────────────────────────────────


def test_future_dated_transactions_are_dropped_and_counted():
    """They can never be applied (the loop stops at today) and silently understate the close."""
    series = replay_scheme(
        [buy(date(2026, 1, 1), "10", "10", "100"), buy(date(2027, 1, 1), "99", "10", "990")],
        NAVS,
        TODAY,
    )
    assert series.end_units == D("10")
    assert series.warnings["future_dated_txn"] == 1


def test_absurdly_old_transaction_dates_are_clamped():
    """A mis-parsed 1900 date would make the day loop 46,000 iterations wide, per scheme."""
    floor = TODAY - timedelta(days=365 * MAX_HISTORY_YEARS)
    assert window_start(date(1900, 1, 1), TODAY) == floor
    assert (TODAY - floor).days < 15_000  # ~40 years, not ~46,000 days
    # A plausible date is left exactly alone.
    assert window_start(date(2026, 1, 1), TODAY) == date(2026, 1, 1)
    assert window_start(date(2027, 1, 1), TODAY) == TODAY  # future clamps to today


def test_opening_balance_seeds_a_partial_period_statement():
    """A CAS covering one recent year opens each scheme at a non-zero balance.

    Replaying from zero understates units and cost for the entire window.
    """
    series = replay_scheme(
        [buy(date(2026, 1, 8), "10", "15", "150")],
        NAVS,
        TODAY,
        opening=Opening(units=D("100"), cost=D("1000"), as_of=date(2026, 1, 1)),
    )
    assert series.end_units == D("110")
    assert series.first_day == date(2026, 1, 1)  # not Jan 8
    assert series.end_cost == D("1150")


def test_a_scheme_with_no_transactions_and_no_opening_contributes_nothing():
    assert replay_scheme([], NAVS, TODAY).value_by_day == {}


# ── Numeric safety ───────────────────────────────────────────────────────────


def test_gain_percentage_is_clamped_to_its_column():
    """Numeric(10,4) tops out at 999999.9999 — a tiny basis must not blow up the INSERT."""
    assert gain_pct(D("1000000000"), D("1")) == MAX_GAIN_PCT


def test_a_wiped_out_position_is_exactly_minus_one_hundred_percent():
    """Value cannot go below zero, so real losses bottom out at -100%.

    The downside clamp in ``gain_pct`` is therefore unreachable from honest data — it
    is there for a corrupted ``total_invested``, not for a bad year.
    """
    assert gain_pct(D("0"), D("1000000000")) == D("-100")


def test_zero_invested_is_zero_gain_not_a_divide_by_zero():
    assert gain_pct(D("500"), D("0")) == D("0")


def test_money_is_clamped_to_its_column():
    assert clamp_money(D("1e30")) == MAX_MONEY


@pytest.mark.parametrize("bad", [None, "", "not-a-number", object()])
def test_garbage_inputs_never_raise(bad):
    """The driver hands us Decimals, but a corrupted row must degrade, not crash."""
    from app.domains.portfolio.services.networth.replay import to_decimal

    assert to_decimal(bad) == D("0")


# ── Roll-up ──────────────────────────────────────────────────────────────────


def test_combine_sums_schemes_and_starts_at_the_earliest_first_day():
    early = replay_scheme([buy(date(2026, 1, 1), "10", "10", "100")], NAVS, TODAY)
    late = replay_scheme([buy(date(2026, 1, 8), "10", "10", "100")], NAVS, TODAY)
    rows, _ = combine({"a": early, "b": late}, TODAY)

    assert rows[0].day == date(2026, 1, 1)
    assert rows[-1].day == TODAY
    assert len(rows) == 10
    # Jan 1: only the early scheme is held.
    assert rows[0].total_value == D("100.00")
    # Jan 10: both, at Rs 15.
    assert rows[-1].total_value == D("300.00")


def test_the_anchor_is_a_no_op_for_a_complete_ledger():
    """For a since-inception statement the residual is zero and nothing shifts."""
    series = replay_scheme([buy(date(2026, 1, 1), "10", "10", "100")], NAVS, TODAY)
    plain, _ = combine({"a": series}, TODAY)
    anchored, _ = combine({"a": series}, TODAY, anchor_value=D("0"), anchor_invested=D("0"))
    assert [r.total_value for r in plain] == [r.total_value for r in anchored]


def test_the_anchor_is_held_flat_across_the_whole_window():
    series = replay_scheme([buy(date(2026, 1, 1), "10", "10", "100")], NAVS, TODAY)
    rows, _ = combine({"a": series}, TODAY, anchor_value=D("500"), anchor_invested=D("400"))
    assert all(r.total_value >= D("500") for r in rows)
    assert rows[0].total_invested == D("500.00")  # 100 ledger + 400 anchor
