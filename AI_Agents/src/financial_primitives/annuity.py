"""Annuity primitives: PMT, RATE (Newton-Raphson inversion), IPMT.

Sign conventions follow numpy_financial: positive principal in, positive payments out.
"""
from __future__ import annotations
import numpy_financial as npf


class RATEConvergenceError(Exception):
    """Newton-Raphson did not converge for RATE inversion."""


def pmt(monthly_rate: float, n: int, principal: float) -> float:
    """Equated monthly payment for a loan. Wraps numpy_financial.pmt with sign-flip."""
    return float(npf.pmt(monthly_rate, n, -principal))


def rate(n: int, payment: float, principal: float, max_iter: int = 100, tol: float = 1e-9) -> float:
    """Inverse of pmt: given n, payment, principal, find the per-period rate.

    Uses npf.rate (Newton-Raphson). Raises RATEConvergenceError on non-convergence
    (NaN result) or when the solver settles on an economically implausible rate
    (e.g. <= -0.5 per period — indicates the inputs admit no real positive rate).
    """
    try:
        result = npf.rate(n, -payment, principal, 0, guess=0.01, tol=tol, maxiter=max_iter)
        if result is None or (isinstance(result, float) and (result != result)):  # NaN check
            raise RATEConvergenceError(f"RATE did not converge for n={n}, pmt={payment}, P={principal}")
        if result <= -0.5:
            raise RATEConvergenceError(
                f"RATE settled on implausible value {result} for n={n}, pmt={payment}, P={principal}"
            )
        return float(result)
    except (ValueError, ZeroDivisionError) as e:
        raise RATEConvergenceError(str(e)) from e


def ipmt(monthly_rate: float, period: int, n: int, principal: float) -> float:
    """Interest portion of EMI for given period (1-indexed)."""
    return float(npf.ipmt(monthly_rate, period, n, -principal))
