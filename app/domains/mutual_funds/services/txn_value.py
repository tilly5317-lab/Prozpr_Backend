"""The rupee value of one MF transaction, healed against a mis-parsed amount.

Single definition shared by every reader of ``mf_transactions`` (snapshot,
holding detail, net-worth series, XIRR, TWR, benchmark) so no two of them can
price the same ledger row differently.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Union

logger = logging.getLogger(__name__)

Number = Union[int, float, Decimal, None]

# A statement's amount column has to agree with units x NAV — CAMS prints all
# three on the same ledger line, and only loads/STT/rounding separate them. Over
# the whole ledger the observed spread is 0.9959–1.0041, so a factor-of-two gap
# is never a real trade.
#
# What it IS: rows the pre-API local parser mis-read. A "*** Stamp Duty ***"
# annotation line shifts its column alignment, and the duty lands in the amount
# column — one HDFC NIFTY500 Multicap purchase of 11,453.263 units at NAV 8.7307
# was filed as Rs 10 instead of Rs 99,995. That priced the units at 0.0009,
# valued a Rs 1.18L holding at a Rs 10 cost basis, and returned 1,177,879.55%,
# which does not fit the NUMERIC(10,4) return columns. Those rows predate the
# move to the CAS Parser API and cannot be re-parsed, so the ledger stays wrong
# and every reader has to price them off units x NAV instead.
_AMOUNT_AGREEMENT_FACTOR = 2.0


def _f(value: Number) -> float:
    if value is None:
        return 0.0
    return float(value)


def trade_value(units: Number, nav: Number, amount: Number) -> float:
    """Magnitude in rupees of one transaction; callers apply the direction.

    Returns ``|amount|`` when the statement's own amount is credible against
    ``|units| x nav``, and ``|units| x nav`` when it is not (or is missing).
    Falls back to ``|amount|`` whenever there is no per-unit price to check
    against — an unpriced row is not evidence that the amount is wrong.
    """
    stated = abs(_f(amount))
    implied = abs(_f(units)) * _f(nav)
    if implied <= 0:
        return stated
    if stated <= 0:
        return implied
    ratio = stated / implied
    if not (1.0 / _AMOUNT_AGREEMENT_FACTOR <= ratio <= _AMOUNT_AGREEMENT_FACTOR):
        # Debug, not warning: these rows are known-bad historical data, and the
        # log alert on the snapshot rebuild fires at every severity.
        logger.debug(
            "transaction amount %r disagrees with units x nav (%r) — pricing at %r",
            stated,
            implied,
            implied,
        )
        return implied
    return stated
