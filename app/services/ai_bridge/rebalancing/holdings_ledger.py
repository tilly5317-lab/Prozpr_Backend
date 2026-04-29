"""Build a per-ISIN list of remaining lots from MfTransaction rows.

FIFO: sells consume the oldest buy-lot first. Switches in/out and dividend
reinvest are NOT yet handled in v1 — extend if needed.

The rebalancing engine works in ISINs but ``MfTransaction`` is keyed by
``scheme_code``, so we resolve ``scheme_code → isin`` via the most recent
non-null ``MfNavHistory.isin`` for that scheme.
"""

from __future__ import annotations

import uuid
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mf.enums import MfTransactionType
from app.models.mf.mf_nav_history import MfNavHistory
from app.models.mf.mf_transaction import MfTransaction


@dataclass(frozen=True)
class Lot:
    """A buy-side lot with units and acquisition cost."""

    acquisition_date: date
    units: Decimal
    acquisition_nav: Decimal


@dataclass(frozen=True)
class HoldingLedgerEntry:
    isin: str
    scheme_code: str
    lots: tuple[Lot, ...]


async def _scheme_to_isin(db: AsyncSession, scheme_codes: set[str]) -> dict[str, str]:
    """Latest non-null ISIN per scheme_code, looked up in MfNavHistory."""
    if not scheme_codes:
        return {}
    rows = (await db.execute(
        select(MfNavHistory.scheme_code, MfNavHistory.isin, MfNavHistory.nav_date)
        .where(MfNavHistory.scheme_code.in_(scheme_codes))
        .where(MfNavHistory.isin.is_not(None))
        .order_by(MfNavHistory.scheme_code, MfNavHistory.nav_date.desc())
    )).all()
    out: dict[str, str] = {}
    for code, isin, _date in rows:
        out.setdefault(code, isin)
    return out


async def build_holdings_ledger(
    db: AsyncSession, *, user_id: uuid.UUID,
) -> list[HoldingLedgerEntry]:
    """Return one entry per ISIN with non-zero remaining units. FIFO."""
    rows = (await db.execute(
        select(MfTransaction)
        .where(MfTransaction.user_id == user_id)
        .order_by(MfTransaction.scheme_code, MfTransaction.transaction_date)
    )).scalars().all()

    by_scheme: dict[str, deque[Lot]] = defaultdict(deque)
    for txn in rows:
        if txn.transaction_type == MfTransactionType.BUY:
            by_scheme[txn.scheme_code].append(Lot(
                acquisition_date=txn.transaction_date,
                units=Decimal(str(txn.units)),
                acquisition_nav=Decimal(str(txn.nav)),
            ))
        elif txn.transaction_type == MfTransactionType.SELL:
            remaining = Decimal(str(txn.units))
            lots = by_scheme[txn.scheme_code]
            while remaining > 0 and lots:
                head = lots[0]
                if head.units <= remaining:
                    remaining -= head.units
                    lots.popleft()
                else:
                    lots[0] = Lot(
                        acquisition_date=head.acquisition_date,
                        units=head.units - remaining,
                        acquisition_nav=head.acquisition_nav,
                    )
                    remaining = Decimal(0)
        # SWITCH_IN / SWITCH_OUT / DIVIDEND_REINVEST: ignored in v1.

    held_schemes = {code for code, lots in by_scheme.items() if lots}
    isin_map = await _scheme_to_isin(db, held_schemes)

    out: list[HoldingLedgerEntry] = []
    for scheme_code, lots in sorted(by_scheme.items()):
        if not lots:
            continue
        isin = isin_map.get(scheme_code)
        if isin is None:
            # No ISIN known for this scheme — skip; service.py logs a warning later.
            continue
        out.append(HoldingLedgerEntry(
            isin=isin,
            scheme_code=scheme_code,
            lots=tuple(lots),
        ))
    return out
