"""The atomic full replace: delete every net-worth row for a user, then write the new one.

This is exactly the requirement — *delete all history first, then calculate it back* —
and it is safe precisely because it all happens inside **one transaction**. Under MVCC a
reader arriving mid-rebuild still sees the previous series until the commit lands, so
"delete everything" never means "the user's chart is empty for the next ninety seconds",
and a crash halfway through leaves the old series completely intact rather than a
half-written one.

That distinction matters because an earlier version got it wrong in the other direction:
its DELETE was confined to the active CAS snapshot while its INSERT covered every day in
the window, so the moment a user had a second statement the insert collided with the
first statement's still-present rows on ``uq_user_nav_history_user_date`` and killed the
backfill at 98%. Deleting by ``user_id`` alone — the table's real owner — cannot collide
with anything, by construction.

Three further properties are deliberate:

* **``pg_advisory_xact_lock``, not ``pg_try_advisory_lock``.** A transaction-scoped lock
  is released on commit *or* rollback, so a worker that dies mid-write cannot leak it.
  Keyed on the user, so two rebuilds for one account serialise while different accounts
  never wait on each other.
* **``unnest`` arrays, not chunked ``VALUES``.** asyncpg caps a statement at 32,767 bind
  parameters, which is why the old upsert chunked at 2,000 rows. Passing three arrays
  makes a twenty-year series three parameters and one round trip, and takes that ceiling
  out of the design entirely.
* **Positions and series state are written in the SAME transaction.** The daily refresh
  reads both; they must never describe different rebuilds.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import Boolean, Date, Numeric, String, bindparam, delete, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.portfolio.models.user_portfolio_nav_history import (
    UserPortfolioNavHistory,
)
from app.domains.portfolio.models.user_scheme_position import UserSchemePosition
from app.domains.portfolio.services.networth.replay import SeriesRow

logger = logging.getLogger(__name__)

# Namespace for the two-argument advisory lock, so net-worth rebuilds cannot collide
# with any other advisory-lock user in the schema.
NETWORTH_LOCK_NAMESPACE = 7421101


@dataclass
class PositionRow:
    scheme_code: str
    units: Decimal
    cost_basis: Decimal
    opening_units: Decimal
    opening_cost: Decimal | None
    opening_as_of: date | None
    ledger_agrees: bool
    first_txn_date: date | None
    last_txn_date: date | None
    nav_key: str | None


@dataclass
class SeriesState:
    first_date: date | None
    last_date: date | None
    row_count: int
    built_from_cas_upload_id: uuid.UUID | None
    ledger_complete: bool
    degraded_schemes: int
    stale_priced_value: Decimal
    anchor_value: Decimal
    anchor_invested: Decimal
    last_invested: Decimal


_INSERT_SERIES = text(
    """
    INSERT INTO user_portfolio_nav_history
          (id, user_id, recorded_date, total_value, total_invested, gain_percentage)
    SELECT gen_random_uuid(), :user_id, t.d, t.v, t.i, t.g
    FROM   unnest(:dates, :values, :invested, :gains) AS t(d, v, i, g)
    """
).bindparams(
    bindparam("dates", type_=ARRAY(Date)),
    bindparam("values", type_=ARRAY(Numeric(18, 2))),
    bindparam("invested", type_=ARRAY(Numeric(18, 2))),
    bindparam("gains", type_=ARRAY(Numeric(10, 4))),
)

_UPSERT_STATE = text(
    """
    INSERT INTO user_networth_series_state
          (user_id, first_date, last_date, row_count, built_at,
           built_from_cas_upload_id, ledger_complete, degraded_schemes,
           stale_priced_value, anchor_value, anchor_invested, last_invested)
    VALUES (:user_id, :first_date, :last_date, :row_count, now(),
            :cas_upload_id, :ledger_complete, :degraded_schemes,
            :stale_priced_value, :anchor_value, :anchor_invested, :last_invested)
    ON CONFLICT (user_id) DO UPDATE SET
        first_date = EXCLUDED.first_date,
        last_date = EXCLUDED.last_date,
        row_count = EXCLUDED.row_count,
        built_at = EXCLUDED.built_at,
        built_from_cas_upload_id = EXCLUDED.built_from_cas_upload_id,
        ledger_complete = EXCLUDED.ledger_complete,
        degraded_schemes = EXCLUDED.degraded_schemes,
        stale_priced_value = EXCLUDED.stale_priced_value,
        anchor_value = EXCLUDED.anchor_value,
        anchor_invested = EXCLUDED.anchor_invested,
        last_invested = EXCLUDED.last_invested,
        updated_at = now()
    """
)

_INSERT_POSITIONS = text(
    """
    INSERT INTO user_scheme_position
          (user_id, scheme_code, units, cost_basis, opening_units, opening_cost,
           opening_as_of, ledger_agrees, first_txn_date, last_txn_date, nav_key)
    SELECT :user_id, t.code, t.units, t.cost, t.open_units, t.open_cost,
           t.open_as_of, t.agrees, t.first_txn, t.last_txn, t.nav_key
    FROM   unnest(:codes, :units, :cost, :open_units, :open_cost,
                  :open_as_of, :agrees, :first_txn, :last_txn, :nav_keys)
           AS t(code, units, cost, open_units, open_cost,
                open_as_of, agrees, first_txn, last_txn, nav_key)
    """
).bindparams(
    bindparam("codes", type_=ARRAY(String)),
    bindparam("units", type_=ARRAY(Numeric(18, 6))),
    bindparam("cost", type_=ARRAY(Numeric(18, 2))),
    bindparam("open_units", type_=ARRAY(Numeric(18, 6))),
    bindparam("open_cost", type_=ARRAY(Numeric(18, 2))),
    bindparam("open_as_of", type_=ARRAY(Date)),
    bindparam("agrees", type_=ARRAY(Boolean)),
    bindparam("first_txn", type_=ARRAY(Date)),
    bindparam("last_txn", type_=ARRAY(Date)),
    bindparam("nav_keys", type_=ARRAY(String)),
)


async def replace_series(
    db: AsyncSession,
    user_id: uuid.UUID,
    rows: list[SeriesRow],
    positions: list[PositionRow],
    state: SeriesState,
) -> int:
    """Atomically swap in a freshly computed series. Returns the row count written.

    Callers must NOT have an open transaction on ``db``: the whole point is that the
    delete and the insert share one, and commit together.
    """
    async with db.begin():
        # Serialise rebuilds for this user only. Transaction-scoped, so it cannot be
        # leaked by a crash — the lock dies with the transaction either way.
        await db.execute(
            text("SELECT pg_advisory_xact_lock(:ns, hashtext(:uid))"),
            {"ns": NETWORTH_LOCK_NAMESPACE, "uid": str(user_id)},
        )

        # Delete by user_id, unqualified. The series belongs to the user, not to any
        # statement — scoping this delete is what broke the rebuild before.
        await db.execute(
            delete(UserPortfolioNavHistory).where(
                UserPortfolioNavHistory.user_id == user_id
            )
        )
        await db.execute(
            delete(UserSchemePosition).where(UserSchemePosition.user_id == user_id)
        )

        if rows:
            await db.execute(
                _INSERT_SERIES,
                {
                    "user_id": user_id,
                    "dates": [r.day for r in rows],
                    "values": [r.total_value for r in rows],
                    "invested": [r.total_invested for r in rows],
                    "gains": [r.gain_percentage for r in rows],
                },
            )

        if positions:
            await db.execute(
                _INSERT_POSITIONS,
                {
                    "user_id": user_id,
                    "codes": [p.scheme_code for p in positions],
                    "units": [p.units for p in positions],
                    "cost": [p.cost_basis for p in positions],
                    "open_units": [p.opening_units for p in positions],
                    "open_cost": [p.opening_cost for p in positions],
                    "open_as_of": [p.opening_as_of for p in positions],
                    "agrees": [bool(p.ledger_agrees) for p in positions],
                    "first_txn": [p.first_txn_date for p in positions],
                    "last_txn": [p.last_txn_date for p in positions],
                    "nav_keys": [p.nav_key for p in positions],
                },
            )

        await db.execute(
            _UPSERT_STATE,
            {
                "user_id": user_id,
                "first_date": state.first_date,
                "last_date": state.last_date,
                "row_count": state.row_count,
                "cas_upload_id": state.built_from_cas_upload_id,
                "ledger_complete": state.ledger_complete,
                "degraded_schemes": state.degraded_schemes,
                "stale_priced_value": state.stale_priced_value,
                "anchor_value": state.anchor_value,
                "anchor_invested": state.anchor_invested,
                "last_invested": state.last_invested,
            },
        )

    logger.info(
        "networth series replaced for user %s: %d days %s..%s (complete=%s degraded=%d)",
        user_id,
        len(rows),
        state.first_date,
        state.last_date,
        state.ledger_complete,
        state.degraded_schemes,
    )
    return len(rows)


async def clear_series(db: AsyncSession, user_id: uuid.UUID) -> None:
    """Remove a user's series entirely — they have no transactions left to build one.

    Without this a user whose ledger disappeared (a corrected re-upload, a reset) keeps
    a ghost chart forever, priced off holdings that no longer exist.
    """
    await replace_series(
        db,
        user_id,
        rows=[],
        positions=[],
        state=SeriesState(
            first_date=None,
            last_date=None,
            row_count=0,
            built_from_cas_upload_id=None,
            ledger_complete=True,
            degraded_schemes=0,
            stale_priced_value=Decimal("0"),
            anchor_value=Decimal("0"),
            anchor_invested=Decimal("0"),
            last_invested=Decimal("0"),
        ),
    )
