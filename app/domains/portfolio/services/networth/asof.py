"""Value the transaction ledger as of one date, without building a whole series.

The series replay walks every calendar day because it has to draw a line. A single
point-in-time figure does not, so this collapses the ledger to per-scheme balances in
one pass and prices each of them once. Same rules as ``replay`` — same unit-sign
handling, same average-cost basis, same "a held position is never worth zero" floor —
just without the day loop.

Used for the fallback headline when a user has transactions but no priced holdings
snapshot, and by the repair script.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.mutual_funds.models import MfNavHistory, MfTransaction
from app.domains.mutual_funds.services.txn_value import trade_value
from app.domains.portfolio.services.networth.clock import ist_today
from app.domains.portfolio.services.networth.loader import resolve_nav_keys
from app.domains.portfolio.services.networth.replay import (
    UNIT_EPSILON,
    UNITS_IN_TYPES,
    UNITS_OUT_TYPES,
    ZERO,
    gain_pct,
    to_decimal,
)

CENTS = Decimal("0.01")
BPS = Decimal("0.0001")


async def compute_networth_as_of(
    db: AsyncSession, user_id: uuid.UUID, as_of: date
) -> tuple[Decimal, Decimal, Decimal] | None:
    """``(total_value, total_invested, gain_pct)`` from the ledger, valued at ``as_of``.

    Returns ``None`` when the user has no transactions on or before ``as_of`` — that is
    "no answer", which is different from "zero", and callers must be able to tell them
    apart.
    """
    rows = (
        await db.execute(
            select(
                MfTransaction.scheme_code,
                MfTransaction.transaction_type,
                MfTransaction.units,
                MfTransaction.nav,
                MfTransaction.amount,
            )
            .where(
                MfTransaction.user_id == user_id,
                MfTransaction.transaction_date <= as_of,
            )
            .order_by(
                MfTransaction.transaction_date.asc(),
                MfTransaction.created_at.asc(),
                MfTransaction.id.asc(),
            )
        )
    ).all()
    if not rows:
        return None

    units_by: dict[str, Decimal] = {}
    cost_by: dict[str, Decimal] = {}
    # Price of last resort per scheme: the NAV the statement itself recorded on the most
    # recent transaction. Used only when nothing is published at or before ``as_of`` —
    # otherwise those units contribute nothing while their cost still counts, which
    # reads as a total loss the user never took.
    stated_nav: dict[str, Decimal] = {}

    for scheme_code, txn_type, units, nav, amount in rows:
        code = (scheme_code or "").strip()
        if not code:
            continue
        magnitude = abs(to_decimal(units))
        value = to_decimal(trade_value(units, nav, amount))
        txn_nav = to_decimal(nav)
        if txn_nav > ZERO:
            stated_nav[code] = txn_nav
        kind = getattr(txn_type, "value", str(txn_type))
        if kind in UNITS_IN_TYPES:
            units_by[code] = units_by.get(code, ZERO) + magnitude
            cost_by[code] = cost_by.get(code, ZERO) + value
        elif kind in UNITS_OUT_TYPES:
            held = units_by.get(code, ZERO)
            if held > UNIT_EPSILON:
                remaining = held - magnitude
                if remaining < ZERO:
                    remaining = ZERO
                cost_by[code] = cost_by.get(code, ZERO) * remaining / held
            units_by[code] = held - magnitude
            if units_by[code] < ZERO:
                units_by[code] = ZERO
                cost_by[code] = ZERO

    live = {c: u for c, u in units_by.items() if u > UNIT_EPSILON}
    if not live:
        return ZERO, ZERO, ZERO

    nav_keys = await resolve_nav_keys(db, set(live))

    total_value = ZERO
    total_invested = ZERO
    for code, units in live.items():
        cost = cost_by.get(code, ZERO)
        if cost > ZERO:
            total_invested += cost
        nav = (
            await db.execute(
                select(MfNavHistory.nav)
                .where(
                    MfNavHistory.scheme_code == nav_keys.get(code, code),
                    MfNavHistory.nav_date <= as_of,
                    MfNavHistory.nav > 0,
                )
                .order_by(MfNavHistory.nav_date.desc())
                .limit(1)
            )
        ).scalar()
        price = to_decimal(nav) if nav is not None else stated_nav.get(code, ZERO)
        if price > ZERO:
            total_value += units * price

    total_value = total_value.quantize(CENTS)
    total_invested = total_invested.quantize(CENTS)
    return (
        total_value,
        total_invested,
        gain_pct(total_value, total_invested).quantize(BPS),
    )


async def compute_today_networth(
    db: AsyncSession, user_id: uuid.UUID
) -> tuple[Decimal, Decimal, Decimal] | None:
    """Today's ledger value in IST. See ``compute_networth_as_of``."""
    return await compute_networth_as_of(db, user_id, ist_today())
