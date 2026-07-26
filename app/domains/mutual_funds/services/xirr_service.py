"""XIRR computation for a user's mutual-fund holdings and overall portfolio.

Pulls cashflows from ``mf_transactions``, prices remaining units at the latest
NAV in ``mf_nav_history``, and computes annualised IRR via
``financial_primitives.xirr``.

Switches between schemes are treated differently at the two grains:

* **Scheme-level XIRR** — SWITCH_IN is real money into the scheme,
  SWITCH_OUT is real money out. Both are included as signed cashflows.
* **Portfolio-level XIRR** — SWITCH_IN/SWITCH_OUT cancel within the portfolio
  (no external money moves). Excluded from cashflows and from the ``invested``
  total to avoid double-counting when a single rupee is switched between funds.
"""

from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from financial_primitives.xirr import xirr

from app.domains.mutual_funds.models.enums import MfTransactionType
from app.domains.mutual_funds.models.mf_nav_history import MfNavHistory
from app.domains.mutual_funds.models.mf_transaction import MfTransaction

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class XirrResult:
    scheme_code: Optional[str]
    folio_number: Optional[str]
    xirr: Optional[float]  # annualised, 0.12 == 12%; None when undefined
    invested: float  # gross outflows (rounded ₹)
    current_value: float  # units_held × latest_nav (rounded ₹)
    absolute_return: Optional[
        float
    ]  # (current + realised − invested) / invested; None if invested == 0
    cashflow_count: int  # number of non-zero cashflows used
    as_of_date: date  # date of the terminal NAV (or latest txn if no NAV found)


async def _latest_navs(
    db: AsyncSession, scheme_codes: set[str]
) -> dict[str, tuple[float, date]]:
    """Most recent ``(nav, nav_date)`` per scheme.

    Resolved in SQL (max-date subquery + join) — fetching every scheme's full
    NAV history to keep one row each pulled ~75k rows per call and pushed the
    /portfolio/twr endpoint past the frontend's 45s request timeout.
    """
    if not scheme_codes:
        return {}
    latest = (
        select(
            MfNavHistory.scheme_code.label("scheme_code"),
            func.max(MfNavHistory.nav_date).label("max_date"),
        )
        .where(MfNavHistory.scheme_code.in_(scheme_codes))
        .group_by(MfNavHistory.scheme_code)
        .subquery()
    )
    rows = (
        await db.execute(
            select(MfNavHistory.scheme_code, MfNavHistory.nav, MfNavHistory.nav_date)
            .join(
                latest,
                (MfNavHistory.scheme_code == latest.c.scheme_code)
                & (MfNavHistory.nav_date == latest.c.max_date),
            )
        )
    ).all()
    return {code: (float(nav), nav_date) for code, nav, nav_date in rows}


def _build_cashflows(
    txns: list[MfTransaction],
    nav_lookup: dict[str, tuple[float, date]],
    *,
    include_switches: bool,
) -> tuple[list[tuple[date, float]], float, float, float, date]:
    """Build the cashflow series for one holding (or the whole portfolio).

    Returns ``(cashflows, invested, realised, current_value, as_of_date)``.
    """
    cfs: list[tuple[date, float]] = []
    invested = 0.0
    realised = 0.0
    units_per_scheme: dict[str, float] = defaultdict(float)

    for t in txns:
        # Ingest stores redemptions (SELL/SWITCH_OUT) with NEGATIVE amount AND units;
        # direction is decided by the transaction type below, so use magnitudes here.
        amount = abs(float(t.amount))
        units = abs(float(t.units))
        ttype = t.transaction_type

        if ttype == MfTransactionType.BUY:
            cfs.append((t.transaction_date, -amount))
            invested += amount
            units_per_scheme[t.scheme_code] += units
        elif ttype == MfTransactionType.SELL:
            cfs.append((t.transaction_date, amount))
            realised += amount
            units_per_scheme[t.scheme_code] -= units
        elif ttype == MfTransactionType.SWITCH_IN:
            if include_switches:
                cfs.append((t.transaction_date, -amount))
                invested += amount
            units_per_scheme[t.scheme_code] += units
        elif ttype == MfTransactionType.SWITCH_OUT:
            if include_switches:
                cfs.append((t.transaction_date, amount))
                realised += amount
            units_per_scheme[t.scheme_code] -= units
        elif ttype == MfTransactionType.DIVIDEND_REINVEST:
            # Dividend declared + reinvested same day → net zero cashflow, units grow.
            units_per_scheme[t.scheme_code] += units

    current_value = 0.0
    as_of_date: Optional[date] = None
    for scheme, units in units_per_scheme.items():
        if units <= 0:
            continue
        nav_entry = nav_lookup.get(scheme)
        if not nav_entry:
            logger.info(
                "xirr: no NAV row for scheme %s; excluded from current value", scheme
            )
            continue
        nav, nav_date = nav_entry
        current_value += units * nav
        if as_of_date is None or nav_date > as_of_date:
            as_of_date = nav_date

    if as_of_date is None:
        as_of_date = max((t.transaction_date for t in txns), default=date.today())

    if current_value > 0:
        cfs.append((as_of_date, current_value))

    return cfs, invested, realised, current_value, as_of_date


def _abs_return(
    invested: float, realised: float, current_value: float
) -> Optional[float]:
    if invested <= 0:
        return None
    return (current_value + realised - invested) / invested


async def compute_scheme_xirr(
    db: AsyncSession,
    user_id: uuid.UUID,
    scheme_code: str,
    folio_number: Optional[str] = None,
) -> XirrResult:
    """XIRR for one scheme (optionally narrowed to one folio)."""
    stmt = (
        select(MfTransaction)
        .where(
            MfTransaction.user_id == user_id, MfTransaction.scheme_code == scheme_code
        )
        .order_by(MfTransaction.transaction_date.asc())
    )
    if folio_number is not None:
        stmt = stmt.where(MfTransaction.folio_number == folio_number)
    txns = list((await db.execute(stmt)).scalars().all())
    nav_lookup = await _latest_navs(db, {scheme_code})

    cfs, invested, realised, cv, as_of = _build_cashflows(
        txns, nav_lookup, include_switches=True
    )
    return XirrResult(
        scheme_code=scheme_code,
        folio_number=folio_number,
        xirr=xirr(cfs),
        invested=round(invested, 2),
        current_value=round(cv, 2),
        absolute_return=_abs_return(invested, realised, cv),
        cashflow_count=len(cfs),
        as_of_date=as_of,
    )


async def compute_portfolio_xirr(db: AsyncSession, user_id: uuid.UUID) -> XirrResult:
    """XIRR across all of a user's MF transactions. Switches are internal — excluded.

    Fetches only the columns ``_build_cashflows`` reads (Row supports the same
    attribute access) — hydrating full ORM entities for thousands of
    transactions dominated the /portfolio/twr response time.
    """
    txns = list(
        (
            await db.execute(
                select(
                    MfTransaction.transaction_date,
                    MfTransaction.transaction_type,
                    MfTransaction.amount,
                    MfTransaction.units,
                    MfTransaction.scheme_code,
                )
                .where(MfTransaction.user_id == user_id)
                .order_by(MfTransaction.transaction_date.asc())
            )
        ).all()
    )
    nav_lookup = await _latest_navs(db, {t.scheme_code for t in txns})

    cfs, invested, realised, cv, as_of = _build_cashflows(
        txns, nav_lookup, include_switches=False
    )
    return XirrResult(
        scheme_code=None,
        folio_number=None,
        xirr=xirr(cfs),
        invested=round(invested, 2),
        current_value=round(cv, 2),
        absolute_return=_abs_return(invested, realised, cv),
        cashflow_count=len(cfs),
        as_of_date=as_of,
    )


async def compute_all_scheme_xirrs(
    db: AsyncSession, user_id: uuid.UUID
) -> list[XirrResult]:
    """One XIRR row per ``(scheme_code, folio_number)`` the user has touched."""
    txns = list(
        (
            await db.execute(
                select(MfTransaction)
                .where(MfTransaction.user_id == user_id)
                .order_by(MfTransaction.transaction_date.asc())
            )
        )
        .scalars()
        .all()
    )
    if not txns:
        return []
    nav_lookup = await _latest_navs(db, {t.scheme_code for t in txns})

    groups: dict[tuple[str, str], list[MfTransaction]] = defaultdict(list)
    for t in txns:
        groups[(t.scheme_code, t.folio_number)].append(t)

    results: list[XirrResult] = []
    for (scheme, folio), group_txns in groups.items():
        cfs, invested, realised, cv, as_of = _build_cashflows(
            group_txns,
            {scheme: nav_lookup[scheme]} if scheme in nav_lookup else {},
            include_switches=True,
        )
        results.append(
            XirrResult(
                scheme_code=scheme,
                folio_number=folio,
                xirr=xirr(cfs),
                invested=round(invested, 2),
                current_value=round(cv, 2),
                absolute_return=_abs_return(invested, realised, cv),
                cashflow_count=len(cfs),
                as_of_date=as_of,
            )
        )
    return results
