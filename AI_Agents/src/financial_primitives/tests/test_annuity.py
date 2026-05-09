import pytest
import numpy_financial as npf
from financial_primitives.annuity import pmt, rate, ipmt


def test_pmt_matches_numpy_financial():
    monthly_rate = (1.085) ** (1/12) - 1
    expected = npf.pmt(monthly_rate, 240, -5_000_000)
    assert pmt(monthly_rate, 240, 5_000_000) == pytest.approx(expected, rel=1e-9)


def test_rate_inverse_of_pmt():
    monthly_rate = 0.0075
    n = 180
    P = 3_000_000
    monthly_emi = pmt(monthly_rate, n, P)
    inferred = rate(n, monthly_emi, P)
    assert inferred == pytest.approx(monthly_rate, rel=1e-6)


def test_rate_non_convergence_raises():
    from financial_primitives.annuity import RATEConvergenceError
    with pytest.raises(RATEConvergenceError):
        rate(n=12, payment=100, principal=1_000_000, max_iter=20)


def test_ipmt_matches_numpy_financial():
    monthly_rate = (1.085) ** (1/12) - 1
    period = 60
    n = 240
    principal = 5_000_000
    expected = npf.ipmt(monthly_rate, period, n, -principal)
    assert ipmt(monthly_rate, period, n, principal) == pytest.approx(expected, rel=1e-9)
