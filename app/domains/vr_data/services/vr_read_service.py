"""The read API the rest of the backend calls. Additive by construction.

Every function here takes one of *our* identifiers (a ``scheme_code``) and
returns ``None`` when VR has nothing — no key configured, no crosswalk link, no
row yet. That contract is the whole safety story for the live environment:

* An existing service can start reading VR **without a fallback branch being
  wrong**. ``rating_for(...) or existing_rating`` behaves exactly as today until
  the mirror is populated.
* Nothing here writes. There is no code path from this module into ``public``,
  so no read of VR data can modify a user's record.
* A failed VR sync degrades to ``None``, not to a stale or a defaulted number.
  That is the opposite of ``_DEFAULT_FUND_RATING = 10`` in
  ``rebal_engine/input_builder.py``, which is the bug this integration exists
  to fix — so treating "no data" as "good" must never be reintroduced here.

Callers should keep using their current source until a value is verified
against VR, and switch one field at a time.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.vr_data.schema import SCHEME_LINK, table_for

logger = logging.getLogger(__name__)


async def _latest_row(
    db: AsyncSession,
    table_name: str,
    scheme_code: str,
    *,
    order_by: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """Newest row of ``table_name`` for the plan linked to ``scheme_code``."""
    table = table_for(table_name)
    stmt = (
        select(table)
        .select_from(
            table.join(SCHEME_LINK, SCHEME_LINK.c.plan_id == table.c.plan_id)
        )
        .where(SCHEME_LINK.c.scheme_code == scheme_code)
        .limit(1)
    )
    if order_by:
        stmt = stmt.order_by(table.c[order_by].desc().nullslast())
    row = (await db.execute(stmt)).mappings().first()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# fund attributes
# ---------------------------------------------------------------------------


async def fund_master(db: AsyncSession, scheme_code: str) -> Optional[dict[str, Any]]:
    """The VR master row — benchmark, lock-in, minimums, SEBI bands, status."""
    return await _latest_row(db, "fund_basic_details", scheme_code)


async def sip_returns(db: AsyncSession, scheme_code: str) -> Optional[dict[str, Any]]:
    """SIP returns and corpus values per horizon, as at the newest date."""
    return await _latest_row(db, "fund_sip_returns", scheme_code, order_by="as_on_date")


async def performance(db: AsyncSession, scheme_code: str) -> Optional[dict[str, Any]]:
    """Daily performance summary — NAV, 52w high/low, nested trailing returns."""
    return await _latest_row(
        db, "fund_performance_details", scheme_code, order_by="nav_date"
    )


async def annual_returns(db: AsyncSession, scheme_code: str) -> list[dict[str, Any]]:
    """Calendar-year returns, newest first. Empty list when unavailable."""
    table = table_for("fund_returns_annual")
    rows = (
        (
            await db.execute(
                select(table.c.year, table.c.returns)
                .select_from(
                    table.join(
                        SCHEME_LINK, SCHEME_LINK.c.plan_id == table.c.plan_id
                    )
                )
                .where(SCHEME_LINK.c.scheme_code == scheme_code)
                .order_by(table.c.year.desc())
            )
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


async def nav_on(
    db: AsyncSession, scheme_code: str, *, adjusted: bool = True
) -> Optional[dict[str, Any]]:
    """Latest NAV point. ``adjusted=True`` prefers ``adjusted_nav``.

    Adjusted NAV is the total-return series — the one every return calculation
    should read, and the field ``mf_nav_history`` has never carried. Falls back
    to raw ``nav`` when VR leaves it null rather than returning nothing.
    """
    row = await _latest_row(db, "nav", scheme_code, order_by="nav_date")
    if not row:
        return None
    value = row.get("adjusted_nav") if adjusted else row.get("nav")
    return {
        "nav_date": row.get("nav_date"),
        "nav": value if value is not None else row.get("nav"),
        "raw_nav": row.get("nav"),
        "adjusted_nav": row.get("adjusted_nav"),
        "is_adjusted": adjusted and row.get("adjusted_nav") is not None,
    }


async def dividends(
    db: AsyncSession, scheme_code: str, *, limit: int = 24
) -> list[dict[str, Any]]:
    """IDCW declarations, newest first — the calendar a raw NAV series needs."""
    table = table_for("fund_dividends")
    rows = (
        (
            await db.execute(
                select(table)
                .select_from(
                    table.join(
                        SCHEME_LINK, SCHEME_LINK.c.plan_id == table.c.plan_id
                    )
                )
                .where(SCHEME_LINK.c.scheme_code == scheme_code)
                .order_by(table.c.div_date.desc().nullslast())
                .limit(limit)
            )
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# transactability
# ---------------------------------------------------------------------------

#: VR's facility flags, in the order a caller usually wants them.
_FACILITY_FLAGS = (
    "scm_pur_available",
    "scm_sip_available",
    "scm_red_available",
    "scm_switch_in_available",
    "scm_switch_out_available",
    "scm_swp_available",
    "scm_stp_available",
)

_AFFIRMATIVE = {"y", "yes", "true", "1", "available", "a"}


async def transactability(
    db: AsyncSession, scheme_code: str
) -> Optional[dict[str, Any]]:
    """Is this scheme currently open to lumpsum / SIP / redemption?

    **Read the freshness caveat before acting on this.** VR documents
    ``fund_transaction_details`` as *irregularly* updated, so a fund that closed
    to subscription today may still read as open here. Treat it as a
    pre-filter — it is right to stop proposing a scheme this says is closed —
    and let the RTA remain the authority at order placement. ``as_of`` is
    returned so a caller can decide the row is too old to trust.
    """
    row = await _latest_row(db, "fund_transaction_details", scheme_code)
    if not row:
        return None

    def flag(name: str) -> Optional[bool]:
        raw = row.get(name)
        if raw is None:
            return None
        return str(raw).strip().lower() in _AFFIRMATIVE

    return {
        "plan_id": row.get("plan_id"),
        "subplan_id": row.get("subplan_id"),
        **{name: flag(name) for name in _FACILITY_FLAGS},
        "transaction_status": row.get("transaction_status"),
        "min_initial_investment": row.get("min_initial_investment"),
        "min_subsequent_investment": row.get("min_subsequent_investment"),
        "min_subsequent_sip_investment": row.get("min_subsequent_sip_investment"),
        "min_investment_multiples": row.get("min_investment_multiples"),
        "max_inv_amount": row.get("max_inv_amount"),
        "sip_swp_stp_details": row.get("sip_swp_stp_details"),
        "as_of": row.get("_vr_synced_at"),
        "freshness_warning": (
            "VR updates fund_transaction_details irregularly; confirm at the RTA "
            "before placing an order."
        ),
    }


# ---------------------------------------------------------------------------
# look-through
# ---------------------------------------------------------------------------


async def holdings(
    db: AsyncSession, scheme_code: str, *, limit: int = 100
) -> list[dict[str, Any]]:
    """Security-level holdings as at the fund's most recent disclosure date."""
    table = table_for("fund_holdings_details")
    rows = (
        (
            await db.execute(
                text(
                    """
                    WITH linked AS (
                        SELECT plan_id FROM vr.scheme_link WHERE scheme_code = :sc
                    ), newest AS (
                        SELECT max(h.as_on_date) AS d
                          FROM vr.fund_holdings_details h
                          JOIN linked l ON l.plan_id = h.plan_id
                    )
                    SELECT h.security_name, h.asset_isin, h.asset_class,
                           h.asset_value, h.num_of_shares, h.asset_percentage,
                           h.coupon_rate, h.maturity, h.rating_name,
                           h.sebi_cap_rank, h.as_on_date
                      FROM vr.fund_holdings_details h
                      JOIN linked l ON l.plan_id = h.plan_id
                      JOIN newest n ON h.as_on_date = n.d
                     ORDER BY h.asset_percentage DESC NULLS LAST
                     LIMIT :limit
                    """
                ),
                {"sc": scheme_code, "limit": limit},
            )
        )
        .mappings()
        .all()
    )
    _ = table  # spec presence check; the raw SQL above is the query
    return [dict(r) for r in rows]


async def sector_allocation(
    db: AsyncSession, scheme_code: str
) -> Optional[dict[str, Any]]:
    """Sector totals for the equity sleeve, as VR's nested object."""
    row = await _latest_row(
        db, "fund_holdings_aggregate_equity", scheme_code, order_by="as_on_date"
    )
    return None if not row else {
        "as_on_date": row.get("as_on_date"),
        "sectors": row.get("sector_details"),
    }


async def debt_profile(db: AsyncSession, scheme_code: str) -> Optional[dict[str, Any]]:
    """Instrument, maturity and credit-rating breakups for the debt sleeve."""
    row = await _latest_row(
        db, "fund_holdings_aggregate_debt", scheme_code, order_by="as_on_date"
    )
    return None if not row else {
        "as_on_date": row.get("as_on_date"),
        "instruments": row.get("holdings_debtstated"),
        "maturity": row.get("holdings_maturity"),
        "ratings": row.get("holdings_rating"),
    }


async def fund_overlap(
    db: AsyncSession, scheme_code_a: str, scheme_code_b: str
) -> Optional[dict[str, Any]]:
    """Percentage overlap between two funds' latest disclosed portfolios.

    The standard definition: sum over shared securities of the smaller of the
    two weights. Needs both funds linked *and* both holdings-loaded, so it
    returns ``None`` rather than a misleading 0% when either side is missing.
    """
    row = (
        await db.execute(
            text(
                """
                WITH a AS (
                    SELECT h.asset_isin, h.asset_percentage
                      FROM vr.fund_holdings_details h
                      JOIN vr.scheme_link l ON l.plan_id = h.plan_id
                     WHERE l.scheme_code = :a
                       AND h.as_on_date = (
                           SELECT max(h2.as_on_date) FROM vr.fund_holdings_details h2
                            WHERE h2.plan_id = h.plan_id)
                ), b AS (
                    SELECT h.asset_isin, h.asset_percentage
                      FROM vr.fund_holdings_details h
                      JOIN vr.scheme_link l ON l.plan_id = h.plan_id
                     WHERE l.scheme_code = :b
                       AND h.as_on_date = (
                           SELECT max(h2.as_on_date) FROM vr.fund_holdings_details h2
                            WHERE h2.plan_id = h.plan_id)
                )
                SELECT (SELECT count(*) FROM a) AS n_a,
                       (SELECT count(*) FROM b) AS n_b,
                       COALESCE(SUM(LEAST(a.asset_percentage, b.asset_percentage)), 0)
                           AS overlap_pct,
                       count(*) AS shared_securities
                  FROM a JOIN b ON a.asset_isin = b.asset_isin
                 WHERE a.asset_isin IS NOT NULL
                """
            ),
            {"a": scheme_code_a, "b": scheme_code_b},
        )
    ).mappings().first()

    if not row or not row["n_a"] or not row["n_b"]:
        return None
    return {
        "overlap_pct": float(row["overlap_pct"] or 0),
        "shared_securities": int(row["shared_securities"] or 0),
        "holdings_a": int(row["n_a"]),
        "holdings_b": int(row["n_b"]),
    }


async def is_available(db: AsyncSession) -> bool:
    """Whether any VR data is usable at all — one query, for a health check."""
    from sqlalchemy import func

    linked = (
        await db.execute(select(func.count()).select_from(SCHEME_LINK))
    ).scalar()
    return bool(linked)
