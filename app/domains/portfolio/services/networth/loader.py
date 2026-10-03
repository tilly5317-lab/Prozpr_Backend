"""Every query that feeds the replay, and nothing else.

Kept apart from ``replay.py`` so the business rules stay pure and testable, and apart
from ``writer.py`` so reads and writes cannot quietly share a transaction.

Two things here are load-bearing for performance:

* **``nav_key`` resolution.** CAS ingest stores whichever identifier it could resolve in
  ``mf_transactions.scheme_code`` — usually an AMFI code, sometimes an ISIN. Pricing an
  ISIN-keyed holding meant ``upper(isin) = :code`` in the inner loop, which no index can
  serve: a sequential scan of a ~10M-row table, once per fund, per rebuild. We resolve
  every identifier to a real ``mf_nav_history.scheme_code`` ONCE, up front, in two
  queries for the whole portfolio.
* **Column subsets, not entities.** A 15-year ledger is thousands of rows; loading them
  as ORM objects puts every one in the identity map and the flush machinery for no
  reason at all. Everything here returns plain tuples and dataclasses.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import String, bindparam, select, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cas_scope import effective_scope, visible_in_snapshot
from app.domains.mutual_funds.models import MfNavHistory, MfTransaction
from app.domains.mutual_funds.models.mf_aa_import import MfAaImport, MfAaSummary
from app.domains.mutual_funds.services.scheme_resolver import (
    build_isin_to_amfi_map,
    canonical_scheme_code,
)
from app.domains.mutual_funds.services.txn_value import trade_value
from app.domains.portfolio.services.networth.replay import Opening, Txn, to_decimal

logger = logging.getLogger(__name__)

ZERO = Decimal("0")


@dataclass
class LedgerScheme:
    """One scheme's transactions, in the order the replay must apply them."""

    scheme_code: str
    txns: list[Txn]
    first_txn_date: date
    last_txn_date: date


@dataclass
class Ledger:
    """Everything the replay needs about a user's transaction history."""

    schemes: dict[str, LedgerScheme]
    nav_keys: dict[str, str]
    openings: dict[str, Opening]
    statement_from: date | None = None
    ledger_complete: bool = True

    @property
    def is_empty(self) -> bool:
        return not self.schemes and not any(
            o.units > ZERO for o in self.openings.values()
        )


def _clean(value: object) -> str | None:
    if value is None:
        return None
    text_value = str(value).strip()
    return text_value or None


def _parse_cas_date(value: object) -> date | None:
    """CAS date columns are free-text strings; accept the formats we actually see."""
    raw = _clean(value)
    if not raw:
        return None
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d-%b-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


async def load_ledger(
    db: AsyncSession,
    user_id: uuid.UUID,
    today: date,
    snapshot_id: uuid.UUID | None = None,
) -> Ledger:
    """Load the user's whole transaction history, grouped and ready to replay.

    Sees exactly one statement's ledger — the ACTIVE CAS upload — plus any unscoped
    rows (manual entries, SimBanks), which is the point: a superseded statement's
    funds must never be summed into the series.

    The statement is pinned HERE, in the query, not only by the CAS read hook. The
    hook is process-global state (listeners installed? ContextVar set?) and a rebuild
    that ran with either missing summed every statement the user ever uploaded: six
    schemes worth Rs 60,792 became 67 schemes worth Rs 15.1 crore. ``snapshot_id``
    defaults to the scope in force, resolving the user's active upload when none is.
    """
    if snapshot_id is None:
        snapshot_id = await effective_scope(db, user_id)
    rows = (
        await db.execute(
            select(
                MfTransaction.scheme_code,
                MfTransaction.transaction_date,
                MfTransaction.transaction_type,
                MfTransaction.units,
                MfTransaction.nav,
                MfTransaction.amount,
            )
            .where(
                MfTransaction.user_id == user_id,
                *visible_in_snapshot(MfTransaction, snapshot_id),
            )
            # ``created_at`` alone is not a tiebreak: the ledger is inserted in
            # 1500-row chunks, so hundreds of rows share a timestamp. Without ``id``
            # a same-day SELL-before-BUY flips between runs and the cost basis — and
            # therefore the whole chart — changes on every rebuild.
            .order_by(
                MfTransaction.scheme_code.asc(),
                MfTransaction.transaction_date.asc(),
                MfTransaction.created_at.asc(),
                MfTransaction.id.asc(),
            )
        )
    ).all()

    schemes: dict[str, LedgerScheme] = {}
    for scheme_code, txn_date, txn_type, units, nav, amount in rows:
        code = _clean(scheme_code)
        if not code or txn_date is None:
            # ``scheme_code`` is NOT NULL with an FK, so this is unreachable in
            # practice — but a blank code would blow up identifier resolution
            # mid-build, and losing one row beats losing the whole series.
            logger.warning(
                "networth: skipping transaction with no scheme_code for user %s",
                user_id,
            )
            continue
        txn = Txn(
            txn_date=txn_date,
            txn_type=getattr(txn_type, "value", str(txn_type)),
            units=to_decimal(units),
            # A mis-parsed amount column is repriced off units x NAV, exactly as every
            # other reader of this ledger prices it.
            nav=to_decimal(nav),
            amount=to_decimal(trade_value(units, nav, amount)),
        )
        existing = schemes.get(code)
        if existing is None:
            schemes[code] = LedgerScheme(
                scheme_code=code,
                txns=[txn],
                first_txn_date=txn_date,
                last_txn_date=txn_date,
            )
        else:
            existing.txns.append(txn)
            if txn_date < existing.first_txn_date:
                existing.first_txn_date = txn_date
            if txn_date > existing.last_txn_date:
                existing.last_txn_date = txn_date

    openings, statement_from, ledger_complete = await load_openings(
        db, user_id, snapshot_id
    )
    nav_keys = await resolve_nav_keys(db, set(schemes) | set(openings))

    return Ledger(
        schemes=schemes,
        nav_keys=nav_keys,
        openings=openings,
        statement_from=statement_from,
        ledger_complete=ledger_complete,
    )


async def load_openings(
    db: AsyncSession, user_id: uuid.UUID, snapshot_id: uuid.UUID | None = None
) -> tuple[dict[str, Opening], date | None, bool]:
    """Per-scheme opening balances from the active statement's raw audit rows.

    A CAS covering only part of the user's history opens each scheme at a non-zero
    balance — ``mf_aa_summaries.opening_bal``, which the ingest already persists
    verbatim. Replaying such a statement from zero understates units and cost for the
    entire window, which is what the old flat "opening position anchor" was papering
    over one constant at a time.

    ``mf_aa_summaries`` is not itself CAS-scoped; its parent ``mf_aa_imports`` is, so
    scoping comes from the join — pinned explicitly to ``snapshot_id`` for the same
    reason ``load_ledger`` pins the ledger. Balances are summed across folios because
    the replay groups by scheme, exactly as ``_derive_scheme_snapshot`` does.

    Returns ``(openings, statement_from, ledger_complete)``. ``ledger_complete`` is
    False when any scheme opened at a non-zero balance — that is the signal that this
    statement cannot tell the whole story on its own.
    """
    if snapshot_id is None:
        snapshot_id = await effective_scope(db, user_id)
    summaries = (
        await db.execute(
            select(
                MfAaSummary.scheme,
                MfAaSummary.isin,
                MfAaSummary.opening_bal,
                MfAaSummary.cost_value,
                MfAaSummary.closing_balance,
                MfAaImport.from_date,
            )
            .join(MfAaImport, MfAaSummary.aa_import_id == MfAaImport.id)
            .where(
                MfAaImport.user_id == user_id,
                *visible_in_snapshot(MfAaImport, snapshot_id),
            )
        )
    ).all()
    if not summaries:
        return {}, None, True

    statement_from: date | None = None
    for row in summaries:
        parsed = _parse_cas_date(row.from_date)
        if parsed is not None and (statement_from is None or parsed < statement_from):
            statement_from = parsed

    # Resolve each summary row to the same canonical code the normalizer gave the
    # transactions, so openings and transactions land on the same scheme.
    keys = {
        value.upper()
        for row in summaries
        for value in (_clean(row.isin), _clean(row.scheme))
        if value and not value.isdigit()
    }
    isin_to_amfi = await build_isin_to_amfi_map(db, keys) if keys else {}

    totals: dict[str, dict[str, Decimal]] = {}
    for row in summaries:
        code = canonical_scheme_code(
            amfi=_clean(row.scheme), isin=_clean(row.isin), isin_to_amfi=isin_to_amfi
        )
        if not code:
            continue
        bucket = totals.setdefault(
            code[:20], {"opening": ZERO, "cost": ZERO, "closing": ZERO}
        )
        bucket["opening"] += to_decimal(row.opening_bal)
        bucket["cost"] += to_decimal(row.cost_value)
        bucket["closing"] += to_decimal(row.closing_balance)

    openings: dict[str, Opening] = {}
    ledger_complete = True
    for code, bucket in totals.items():
        opening_units = bucket["opening"]
        if opening_units <= ZERO:
            continue
        ledger_complete = False
        # The CAS gives an opening *balance* but no opening *cost* — ``cost_value`` is
        # the cost of the CLOSING position. Apportioning it by units is the only honest
        # estimate available, and it is bounded by the statement's own numbers.
        closing_units = bucket["closing"]
        opening_cost: Decimal | None = None
        if closing_units > ZERO and bucket["cost"] > ZERO:
            opening_cost = bucket["cost"] * opening_units / closing_units
        openings[code] = Opening(
            units=opening_units,
            cost=opening_cost,
            as_of=statement_from,
            ledger_agrees=True,
        )

    return openings, statement_from, ledger_complete


async def resolve_nav_keys(db: AsyncSession, scheme_codes: set[str]) -> dict[str, str]:
    """Map each holding identifier to the ``mf_nav_history.scheme_code`` that prices it.

    Two queries for the whole portfolio, replacing an unindexable ``upper(isin)``
    comparison inside the per-day loop. Codes that resolve to nothing map to themselves
    so the caller still tries — and the replay flags the fund as unpriced rather than
    silently valuing a real position at zero.
    """
    codes = {c for c in (_clean(c) for c in scheme_codes) if c}
    if not codes:
        return {}

    code_list = sorted(codes)
    resolved: dict[str, str] = {}

    direct = set(
        (
            await db.execute(
                select(MfNavHistory.scheme_code)
                .where(MfNavHistory.scheme_code.in_(code_list))
                .distinct()
            )
        )
        .scalars()
        .all()
    )
    for code in code_list:
        if code in direct:
            resolved[code] = code

    unresolved = [c for c in code_list if c not in resolved]
    if unresolved:
        # Hits ``ix_mf_nav_isin_upper``. DISTINCT ON keeps one scheme_code per ISIN —
        # without it a fund re-listed under two AMFI codes returns both and the choice
        # is whatever the planner emitted last.
        rows = (
            await db.execute(
                text(
                    "SELECT DISTINCT ON (upper(isin)) upper(isin) AS key, scheme_code "
                    "FROM mf_nav_history "
                    "WHERE upper(isin) = ANY(:codes) "
                    "ORDER BY upper(isin), nav_date DESC"
                ).bindparams(bindparam("codes", type_=ARRAY(String))),
                {"codes": [c.upper() for c in unresolved]},
            )
        ).all()
        by_isin = {row.key: row.scheme_code for row in rows}
        for code in unresolved:
            resolved[code] = by_isin.get(code.upper(), code)

    return resolved


async def load_navs(
    db: AsyncSession, nav_key: str, upto: date
) -> list[tuple[date, Decimal]]:
    """Ascending, deduplicated, strictly positive NAV history for one scheme.

    ``nav <= 0`` is missing data, not a price: carrying it would value a live holding
    at nothing for every day until the next publication.
    """
    rows = (
        await db.execute(
            select(MfNavHistory.nav_date, MfNavHistory.nav)
            .where(
                MfNavHistory.scheme_code == nav_key,
                MfNavHistory.nav_date <= upto,
                MfNavHistory.nav > 0,
            )
            .order_by(MfNavHistory.nav_date.asc())
        )
    ).all()
    # ``uq_mf_nav_scheme_date`` already guarantees one row per (scheme, date), but the
    # replay's forward-carry depends on it absolutely — assert it cheaply rather than
    # trust a constraint a future migration might relax.
    out: list[tuple[date, Decimal]] = []
    for nav_date, nav in rows:
        if out and out[-1][0] == nav_date:
            continue
        out.append((nav_date, to_decimal(nav)))
    return out
