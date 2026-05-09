from datetime import date
import pytest
from financial_primitives.inflation import inflate, real_rate
from financial_primitives.dates import fy_for_date, fy_end_after, eomonth, year_fraction
from financial_primitives.retirement import retirement_corpus_pv


def test_inflate():
    assert inflate(100_000, rate=0.06, years=5) == pytest.approx(133_822.5577, rel=1e-6)


def test_real_rate_fisher():
    r = real_rate(nominal=0.09, inflation=0.06)
    assert r == pytest.approx((1.09 / 1.06) - 1, rel=1e-9)


def test_fy_for_date_indian():
    assert fy_for_date(date(2026, 3, 31)) == 2026
    assert fy_for_date(date(2026, 4, 1)) == 2027
    assert fy_for_date(date(2026, 12, 15)) == 2027


def test_fy_end_after():
    assert fy_end_after(date(2026, 3, 31)) == date(2026, 3, 31)
    assert fy_end_after(date(2026, 4, 1)) == date(2027, 3, 31)
    assert fy_end_after(date(2026, 12, 15)) == date(2027, 3, 31)


def test_eomonth():
    assert eomonth(date(2026, 5, 9)) == date(2026, 5, 31)
    assert eomonth(date(2026, 2, 15)) == date(2026, 2, 28)
    assert eomonth(date(2024, 2, 15)) == date(2024, 2, 29)


def test_year_fraction():
    assert year_fraction(date(2026, 5, 9), date(2027, 5, 9)) == pytest.approx(1.0, rel=1e-3)


def test_retirement_corpus_pv():
    expected_via_npf = 1_000_000 * ((1 - (1.03) ** -25) / 0.03)
    actual = retirement_corpus_pv(
        annual_expense_fv=1_000_000,
        post_retirement_years=25,
        real_roi_annual=0.03,
    )
    assert actual == pytest.approx(expected_via_npf, rel=1e-6)
