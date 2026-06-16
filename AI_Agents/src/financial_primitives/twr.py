"""Time-weighted return (TWR) — the daily-linked wealth index.

Pure kernel: given a dense daily series of portfolio values and the net external
cashflow on each date, link the per-day growth factors into a "growth of 1"
wealth index. TWR strips out the size/timing of contributions (unlike a
money-weighted return such as XIRR), so it isolates investment performance.

    r_t = (V_t - C_t) / V_{t-1} - 1        (C_t > 0 money in, < 0 money out)
    W_t = W_{t-1} * (1 + r_t)              (W anchored at 1.0 on day one)

The total time-weighted return over the whole series is ``W[-1] - 1``; any
sub-window return is ``W_end / W_start - 1``.
"""

from datetime import date

_EPS = 1e-9


def twr_wealth_index(
    daily_values: list[tuple[date, float]],
    daily_cashflows: dict[date, float],
) -> list[tuple[date, float]]:
    """Return ``[(date, wealth_index)]`` anchored at 1.0 on the first day.

    ``daily_values``: ``(date, value)`` ascending, one entry per day.
    ``daily_cashflows``: net external cashflow per date (+ money in, − money out);
    dates with no external flow may be omitted.

    When the prior value is ~0 (the first day, or a re-entry after a full exit)
    there is no base to grow from: the day's return is 0 (the index carries flat)
    and that day seeds a fresh base — avoiding a divide-by-zero.
    """
    out: list[tuple[date, float]] = []
    w = 1.0
    prev_value: float | None = None
    for d, v in daily_values:
        c = daily_cashflows.get(d, 0.0)
        if prev_value is None or prev_value <= _EPS:
            r = 0.0
        else:
            r = (v - c) / prev_value - 1.0
        w *= 1.0 + r
        out.append((d, w))
        prev_value = v
    return out
