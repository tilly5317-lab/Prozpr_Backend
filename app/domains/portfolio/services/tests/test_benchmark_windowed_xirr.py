"""Unit tests for the windowed-XIRR math powering the Performance box.

Pure-function tests (no DB): synthetic NAV/TRI lookups with closed-form answers.
"""

from datetime import date

from app.domains.portfolio.services.benchmark_service import (
    TxnLite,
    compute_windowed_xirrs,
)

FIRST = date(2022, 1, 1)


def _nav(_scheme: str, on: date) -> float:
    """Customer NAV grows a clean 10%/yr from 10.0 at inception."""
    years = (on - FIRST).days / 365.0
    return 10.0 * (1.10 ** years)


def _tri(on: date) -> float:
    """Nifty TRI grows a clean 20%/yr from 10.0 at inception."""
    years = (on - FIRST).days / 365.0
    return 10.0 * (1.20 ** years)


def test_windowed_xirr_customer_and_nifty():
    # One ₹100 buy of 10 units at inception; held untouched for exactly 2 years.
    txns = [TxnLite(txn_date=FIRST, txn_type="BUY", amount=100.0, units=10.0, scheme_code="X")]
    out = compute_windowed_xirrs(
        txns, _nav, _tri, as_of=date(2024, 1, 1), windows=["1Y", "All"]
    )

    c_all, n_all = out["All"]
    c_1y, n_1y = out["1Y"]
    # Customer: 10%/yr → XIRR ~0.10 both since-inception and over a trailing year.
    assert abs(c_all - 0.10) < 1e-3
    assert abs(c_1y - 0.10) < 1e-3
    # Nifty clone (same ₹ flows): 20%/yr → XIRR ~0.20.
    assert abs(n_all - 0.20) < 1e-3
    assert abs(n_1y - 0.20) < 1e-3


def test_windowed_xirr_no_transactions():
    out = compute_windowed_xirrs([], _nav, _tri, as_of=date(2024, 1, 1), windows=["1Y", "All"])
    assert out == {"1Y": (None, None), "All": (None, None)}


def test_windowed_xirr_sell_sign_invariant():
    # A redemption stored CAS-negative must give the SAME XIRR as the positive form
    # (type drives direction; magnitudes are taken). Guards the amount-sign bug.
    buy = TxnLite(txn_date=FIRST, txn_type="BUY", amount=100.0, units=10.0, scheme_code="X")
    sell_neg = TxnLite(txn_date=date(2023, 1, 1), txn_type="SELL", amount=-60.0, units=-5.0, scheme_code="X")
    sell_pos = TxnLite(txn_date=date(2023, 1, 1), txn_type="SELL", amount=60.0, units=5.0, scheme_code="X")

    as_of = date(2024, 1, 1)
    neg = compute_windowed_xirrs([buy, sell_neg], _nav, _tri, as_of=as_of, windows=["All", "1Y"])
    pos = compute_windowed_xirrs([buy, sell_pos], _nav, _tri, as_of=as_of, windows=["All", "1Y"])

    assert neg["All"][0] == pos["All"][0]
    assert neg["1Y"][0] == pos["1Y"][0]
