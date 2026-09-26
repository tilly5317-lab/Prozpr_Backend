"""``trade_value`` — the one definition of what a ledger row is worth."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.domains.mutual_funds.services.txn_value import trade_value


def test_consistent_row_is_taken_at_face_value():
    """The overwhelming majority: amount == units x NAV to within rounding."""
    assert trade_value(43_186.922, 9.2616, 399_980.00) == pytest.approx(399_980.00)


def test_stamp_duty_in_the_amount_column_is_repriced():
    """Production scheme 152778: Rs 10 duty filed as a Rs 99,995 purchase."""
    assert trade_value(11_453.263, 8.7307, 10.00) == pytest.approx(99_995.00, abs=0.05)


def test_redemption_magnitudes_ignore_the_negative_units():
    """CAS writes redemption units negative; callers apply direction themselves."""
    assert trade_value(-8_750.0, 123.6521, 5.00) == pytest.approx(1_081_955.88, abs=0.5)


def test_stamp_duty_sized_gap_is_not_repriced():
    """A purchase amount net of duty differs by ~0.005% — well inside the band."""
    assert trade_value(11_453.263, 8.7307, 99_995.00) == pytest.approx(99_995.00)


def test_missing_nav_leaves_the_amount_alone():
    """An unpriced row is no evidence that its amount is wrong."""
    assert trade_value(100.0, None, 12_345.0) == pytest.approx(12_345.0)
    assert trade_value(100.0, 0.0, 12_345.0) == pytest.approx(12_345.0)


def test_missing_amount_falls_back_to_units_times_nav():
    assert trade_value(100.0, 12.5, 0.0) == pytest.approx(1_250.0)
    assert trade_value(100.0, 12.5, None) == pytest.approx(1_250.0)


def test_nothing_to_go_on_is_zero():
    assert trade_value(None, None, None) == 0.0


def test_decimal_inputs_are_accepted():
    """Callers pass ORM columns straight through — these are Decimals."""
    value = trade_value(Decimal("11453.2630"), Decimal("8.7307"), Decimal("10.00"))
    assert value == pytest.approx(99_995.00, abs=0.05)
