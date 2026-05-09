import pytest
from financial_primitives.time_value import future_value, present_value, compound


def test_future_value_basic():
    # 100,000 at 8% for 10 years → 100000 * 1.08^10 ≈ 215,892.50
    assert future_value(100_000, rate=0.08, years=10) == pytest.approx(215_892.50, rel=1e-6)


def test_present_value_basic():
    # 215,892.50 discounted at 8% for 10 years → 100,000
    assert present_value(215_892.50, rate=0.08, years=10) == pytest.approx(100_000, rel=1e-6)


def test_fv_pv_inverse():
    pv = 50_000
    rate = 0.07
    years = 15
    assert present_value(future_value(pv, rate, years), rate, years) == pytest.approx(pv, rel=1e-9)


def test_compound_monthly():
    # 100,000 monthly compounded at 12% annual for 1 year
    # monthly_rate = (1.12)^(1/12) - 1 ≈ 0.00949
    # FV = 100000 * (1 + 0.00949)^12 = 100000 * 1.12 = 112000
    assert compound(100_000, monthly_rate=(1.12 ** (1/12) - 1), months=12) == pytest.approx(112_000, rel=1e-6)


def test_zero_years():
    assert future_value(100_000, rate=0.08, years=0) == 100_000
    assert present_value(100_000, rate=0.08, years=0) == 100_000


def test_negative_years_raises():
    with pytest.raises(ValueError):
        future_value(100_000, rate=0.08, years=-1)
