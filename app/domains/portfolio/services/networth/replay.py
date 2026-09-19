"""Pure net-worth replay: transactions x units x that day's NAV.

No database, no I/O, no clock — every input is passed in. That is deliberate: this
module holds all of the business rules, so keeping it pure is what lets every edge case
be a table-driven unit test instead of an integration fixture.

The rules, and why each one exists:

* **CAS stores redemption units as a negative number.** Take the magnitude and let the
  transaction *type* carry the sign. ``units -= (-x)`` adds the units back and inflates
  the balance — that was the Rs 1.33cr-vs-Rs 1.07cr bug.
* **Never trust the amount column.** A "*** Stamp Duty ***" annotation line shifts the
  parser's columns, so a Rs 99,995 purchase can be filed as Rs 10. ``trade_value``
  reprices off units x NAV whenever the two disagree by more than 2x; callers must pass
  the repriced figure in ``Txn.amount``.
* **Average-cost basis on a sell.** Reduce the basis by the *cost* of the units sold,
  proportionally, before decrementing units. Reducing by the redemption *proceeds*
  drives ``invested`` negative on a profitable sale and corrupts gain %.
* **A held position is never worth zero.** 107 of 850 held schemes have no published NAV
  at or before their first transaction. Seeding the carried price from the transaction's
  own stated NAV is the honest floor — it is what the user actually paid that day. It
  only ever replaces a zero; a published NAV always wins.
* **Forward-carry NAV** across weekends, holidays and publication gaps, so every
  calendar day has a well-defined value.
* **Money is Decimal.** ``units``/``nav``/``amount`` are all Postgres ``Numeric`` and
  arrive as ``Decimal``. Casting to float meant the repeated proportional basis
  reduction accumulated binary drift over thousands of transactions.
* **Each scheme starts at its CAS opening balance.** A statement covering only part of
  the user's history opens each scheme at a non-zero balance; replaying from zero
  understates both units and cost for the entire window. For a since-inception
  statement the opening balance is 0 and this is a no-op.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

ZERO = Decimal("0")

# Units below this are parse dust, not a position. ``mf_transactions.units`` is
# Numeric(18,4), so anything under a millionth of a unit is noise by construction.
UNIT_EPSILON = Decimal("0.000001")

# ``total_value`` / ``total_invested`` are Numeric(18,2); ``gain_percentage`` is
# Numeric(10,4). A corrupt NAV must not make Postgres raise mid-INSERT and take the
# whole rebuild down — clamp, count it, and carry on.
MAX_MONEY = Decimal("9999999999999999.99")
MAX_GAIN_PCT = Decimal("999999.9999")

# A published NAV older than this while units are still held is not a current price.
# We keep carrying it (there is nothing better) but the series is marked degraded so
# the UI can qualify it instead of presenting a months-old number as today's truth.
STALE_NAV_DAYS = 30

# The replay window floor. A mis-parsed 1900 transaction date would otherwise make the
# day loop 46,000 iterations wide, per scheme.
MAX_HISTORY_YEARS = 40

UNITS_IN_TYPES = frozenset({"BUY", "SWITCH_IN", "DIVIDEND_REINVEST"})
UNITS_OUT_TYPES = frozenset({"SELL", "SWITCH_OUT"})


@dataclass(frozen=True)
class Txn:
    """One unit-moving transaction, already repriced through ``trade_value``."""

    txn_date: date
    txn_type: str
    units: Decimal  # signed as the CAS printed it; the magnitude is taken here
    nav: Decimal
    amount: Decimal  # trade_value(units, nav, amount)


@dataclass(frozen=True)
class Opening:
    """A scheme's balance at the start of a partial-period statement."""

    units: Decimal = ZERO
    cost: Decimal | None = None
    as_of: date | None = None
    # Ingest's ``derived_from_txns``: did the ledger reproduce the statement's own
    # closing balance? False marks a scheme whose transaction history is incomplete.
    ledger_agrees: bool = True


@dataclass
class SchemeSeries:
    """One scheme's contribution to the portfolio series."""

    value_by_day: dict[date, Decimal] = field(default_factory=dict)
    invested_by_day: dict[date, Decimal] = field(default_factory=dict)
    first_day: date | None = None
    end_units: Decimal = ZERO
    end_cost: Decimal = ZERO
    end_value: Decimal = ZERO
    # True when the closing price is a stated or stale NAV, not a fresh published one.
    degraded: bool = False
    stale_value: Decimal = ZERO
    warnings: Counter = field(default_factory=Counter)


def to_decimal(value: object) -> Decimal:
    """Coerce whatever the driver hands us into a Decimal, never raising."""
    if value is None:
        return ZERO
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return ZERO


def clamp_money(value: Decimal, warnings: Counter | None = None) -> Decimal:
    """Keep a rupee figure inside Numeric(18,2) instead of letting the INSERT raise."""
    if value > MAX_MONEY:
        if warnings is not None:
            warnings["value_clamped"] += 1
        return MAX_MONEY
    if value < -MAX_MONEY:
        if warnings is not None:
            warnings["value_clamped"] += 1
        return -MAX_MONEY
    return value


def gain_pct(
    total: Decimal, invested: Decimal, warnings: Counter | None = None
) -> Decimal:
    """Gain %, clamped to Numeric(10,4). Zero invested is zero gain, not a divide."""
    if invested <= ZERO:
        return ZERO
    pct = (total - invested) / invested * Decimal(100)
    if pct > MAX_GAIN_PCT:
        if warnings is not None:
            warnings["gain_clamped"] += 1
        return MAX_GAIN_PCT
    if pct < -MAX_GAIN_PCT:
        if warnings is not None:
            warnings["gain_clamped"] += 1
        return -MAX_GAIN_PCT
    return pct


def window_start(first_txn: date, today: date) -> date:
    """Clamp the replay window to something a human could plausibly have lived."""
    floor = today - timedelta(days=365 * MAX_HISTORY_YEARS)
    if first_txn < floor:
        return floor
    if first_txn > today:
        return today
    return first_txn


def replay_scheme(
    txns: list[Txn],
    navs: list[tuple[date, Decimal]],
    today: date,
    opening: Opening | None = None,
) -> SchemeSeries:
    """Day-by-day value and cost basis for ONE scheme.

    ``navs`` must be ascending, deduplicated by date, and already filtered to
    ``nav > 0`` and ``nav_date <= today`` — a zero or negative NAV is missing data, not
    a price, and carrying it would value a live holding at nothing.
    """
    out = SchemeSeries()

    # Transactions dated in the future are a parse error. They can never be applied
    # (the loop stops at today) but silently leaving them in understates the closing
    # position against the holdings snapshot, so drop them and say so.
    usable: list[Txn] = []
    for txn in txns:
        if txn.txn_date > today:
            out.warnings["future_dated_txn"] += 1
            continue
        usable.append(txn)

    opening = opening or Opening()
    has_opening = opening.units > UNIT_EPSILON and opening.as_of is not None

    if not usable and not has_opening:
        return out

    candidates = [t.txn_date for t in usable]
    if has_opening and opening.as_of is not None:
        candidates.append(opening.as_of)
    start = window_start(min(candidates), today)
    if start > today:
        return out
    out.first_day = start

    units = opening.units if has_opening else ZERO
    invested = (
        to_decimal(opening.cost) if (has_opening and opening.cost is not None) else ZERO
    )

    last_nav = ZERO
    last_nav_day: date | None = None
    priced_off_statement = False

    txn_i = 0
    nav_i = 0
    n_txn = len(usable)
    n_nav = len(navs)

    day = start
    while day <= today:
        # 1. Apply every transaction dated on this day, in the caller's order.
        while txn_i < n_txn and usable[txn_i].txn_date == day:
            txn = usable[txn_i]
            magnitude = abs(to_decimal(txn.units))
            txn_nav = to_decimal(txn.nav)
            # The statement's own price per unit is the floor of last resort — used
            # only while no published NAV exists yet, never over one.
            if last_nav <= ZERO and txn_nav > ZERO:
                last_nav = txn_nav
                priced_off_statement = True
            if txn.txn_type in UNITS_IN_TYPES:
                units += magnitude
                invested += to_decimal(txn.amount)
            elif txn.txn_type in UNITS_OUT_TYPES:
                if units > UNIT_EPSILON:
                    remaining = units - magnitude
                    if remaining < ZERO:
                        remaining = ZERO
                    invested = invested * remaining / units
                units -= magnitude
                if units < ZERO:
                    # A partial ledger can redeem units it never saw bought. Clamp
                    # rather than carry a negative position into the valuation.
                    out.warnings["negative_units_clamped"] += 1
                    units = ZERO
                    invested = ZERO
            txn_i += 1

        # 2. Carry the latest published NAV at or behind this day forward.
        while nav_i < n_nav and navs[nav_i][0] <= day:
            last_nav = navs[nav_i][1]
            last_nav_day = navs[nav_i][0]
            priced_off_statement = False
            nav_i += 1

        held = units if units > UNIT_EPSILON else ZERO
        if held > ZERO:
            out.value_by_day[day] = held * last_nav if last_nav > ZERO else ZERO
            out.invested_by_day[day] = invested if invested > ZERO else ZERO
        else:
            # A fully redeemed position is worth nothing AND costs nothing. Leaving the
            # cost behind reads as a 100% loss the user never took.
            out.value_by_day[day] = ZERO
            out.invested_by_day[day] = ZERO

        day += timedelta(days=1)

    out.end_units = units if units > UNIT_EPSILON else ZERO
    out.end_cost = invested if (out.end_units > ZERO and invested > ZERO) else ZERO
    out.end_value = out.value_by_day.get(today, ZERO)

    if out.end_units > ZERO:
        if last_nav <= ZERO:
            # Nothing priced this fund at all — the position is real but invisible.
            out.degraded = True
            out.warnings["unpriced_scheme"] += 1
        elif priced_off_statement or last_nav_day is None:
            out.degraded = True
            out.stale_value = out.end_value
            out.warnings["priced_off_statement"] += 1
        elif (today - last_nav_day).days > STALE_NAV_DAYS:
            out.degraded = True
            out.stale_value = out.end_value
            out.warnings["stale_nav"] += 1

    return out


@dataclass(frozen=True)
class SeriesRow:
    """One persisted day of the portfolio series."""

    day: date
    total_value: Decimal
    total_invested: Decimal
    gain_percentage: Decimal


CENTS = Decimal("0.01")
BPS = Decimal("0.0001")


def combine(
    per_scheme: dict[str, SchemeSeries],
    today: date,
    *,
    anchor_value: Decimal = ZERO,
    anchor_invested: Decimal = ZERO,
) -> tuple[list[SeriesRow], Counter]:
    """Roll every scheme's daily contribution into one portfolio series.

    Days before a scheme's own first day simply contribute nothing, so a portfolio
    built up over years opens at the first purchase and grows as funds are added.

    ``anchor_value`` / ``anchor_invested`` are the opening-position adjustment for a
    ledger the active statement could not fully cover (see ``anchor`` in the service).
    They are held flat across the window so the series stays continuous and ends exactly
    at the authoritative headline. For a complete ledger both are zero and this is a
    no-op — which is the property that makes the anchor safe to apply unconditionally.
    """
    warnings: Counter = Counter()
    first_days = [s.first_day for s in per_scheme.values() if s.first_day is not None]
    if not first_days:
        return [], warnings

    for series in per_scheme.values():
        warnings.update(series.warnings)

    start = min(first_days)
    value_by_day: dict[date, Decimal] = {}
    invested_by_day: dict[date, Decimal] = {}
    for series in per_scheme.values():
        for day, value in series.value_by_day.items():
            value_by_day[day] = value_by_day.get(day, ZERO) + value
        for day, cost in series.invested_by_day.items():
            invested_by_day[day] = invested_by_day.get(day, ZERO) + cost

    rows: list[SeriesRow] = []
    day = start
    while day <= today:
        total = clamp_money(value_by_day.get(day, ZERO) + anchor_value, warnings)
        invested = clamp_money(
            invested_by_day.get(day, ZERO) + anchor_invested, warnings
        )
        rows.append(
            SeriesRow(
                day=day,
                total_value=total.quantize(CENTS),
                total_invested=invested.quantize(CENTS),
                gain_percentage=gain_pct(total, invested, warnings).quantize(BPS),
            )
        )
        day += timedelta(days=1)

    return rows, warnings
