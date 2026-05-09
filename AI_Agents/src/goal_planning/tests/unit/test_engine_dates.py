from datetime import date
import pytest
from goal_planning.engine.dates import (
    _round_thousand, near_term_cutoff, medium_term_cutoff, real_roi_monthly,
)


def test_round_thousand():
    assert _round_thousand(12_500) == 13_000
    assert _round_thousand(12_499) == 12_000
    assert _round_thousand(12_501) == 13_000
    assert _round_thousand(0) == 0
    assert _round_thousand(-1_500) == -2_000


def test_near_term_cutoff_24_months_then_fy_end():
    # latest_update = 2026-05-09 → +24 months = 2028-05-09 → fy_end_after = 2029-03-31
    assert near_term_cutoff(date(2026, 5, 9)) == date(2029, 3, 31)


def test_medium_term_cutoff_36_months_after_near():
    # near = 2029-03-31; +36 months = 2032-03-31 (already FY end)
    assert medium_term_cutoff(near_term_end=date(2029, 3, 31)) == date(2032, 3, 31)


def test_real_roi_monthly():
    expected_annual = (1.09 / 1.06) - 1
    expected_monthly = (1 + expected_annual) ** (1/12) - 1
    assert real_roi_monthly(roi_nominal=0.09, inflation=0.06) == pytest.approx(expected_monthly, rel=1e-9)
