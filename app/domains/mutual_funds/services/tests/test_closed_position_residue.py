"""A fully-exited fund must not be treated as an open position.

Buying and selling the same units cancels to ~0 in float, not 0 — DSP Midcap
with 2,591.249 units each way left 4.5e-13. A `units <= 0` guard lets that
through, and `invested / units` then divides a realised profit by the residue:
-87,350 / 4.5e-13 = -1.9e17, which overflows avg_nav's NUMERIC(12,4). The insert
raised, and with no per-user isolation the nightly rebuild wrote nothing for
anyone from 2026-05-07 to 2026-08-02.
"""

from __future__ import annotations

from app.domains.mutual_funds.services.latest_snapshot_service import (
    _CLOSED_POSITION_UNITS,
)

# The real transaction set that broke production (user 2e88d399, scheme 104481).
_BOUGHT = [356.138, 441.314, 558.809, 412.773, 415.255, 406.960]
_SOLD = [441.314, 2149.935]


def _net_units() -> float:
    """Accumulate exactly as rebuild_user_latest_snapshot does."""
    units = 0.0
    for u in _BOUGHT:
        units += u
    for u in _SOLD:
        units -= u
    return units


def test_a_fully_exited_fund_does_not_cancel_to_zero_in_float():
    """The premise. If this ever fails the bug is gone for a different reason."""
    residue = _net_units()

    assert residue != 0.0
    assert residue > 0.0, "positive residue is what defeats a `units <= 0` guard"
    assert abs(residue) < 1e-9


def test_the_epsilon_guard_catches_the_residue():
    assert _net_units() <= _CLOSED_POSITION_UNITS


def test_the_old_guard_did_not():
    """Documents the exact hole: `units <= 0` is False for 4.5e-13."""
    assert not (_net_units() <= 0)


def test_dividing_by_the_residue_overflows_the_column():
    """avg_nav is NUMERIC(12,4) — max ~1e8. Shows why the insert raised."""
    invested = -87_350.25          # sold for more than bought: a realised profit

    avg_nav = invested / _net_units()

    assert abs(avg_nav) > 1e8


def test_a_real_holding_is_far_above_the_threshold():
    """The guard must not swallow genuine small positions."""
    assert 0.001 > _CLOSED_POSITION_UNITS
