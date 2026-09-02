"""Resolve VR ``plan_id`` against our ``mf_fund_metadata.scheme_code``.

This is the only place vendor data and product data meet, and it is deliberately
one table (``vr.scheme_link``) rather than a column added to
``mf_fund_metadata``. Adding a ``plan_id`` column to a live table would be a
schema change on the user-facing path for a mapping that is a guess until it is
verified; a side table can be rebuilt, audited and thrown away without touching
anything a request handler reads.

Two match methods, most trustworthy first:

``isin``
    ``vr.subplan_isin.isin_code`` against ``mf_fund_metadata.isin``. ISIN is a
    registered identifier, so a hit is exact. This is also the join CAS ingest
    already relies on, which is why ``subplan_isin`` is in the core tier.
``amfi_code``
    ``vr.fund_basic_details.amfi_code`` against ``mf_fund_metadata.scheme_code``
    — our ``scheme_code`` is the AMFI code, as populated by ``mfapi_fetcher``.
    Exact when present, but VR leaves ``amfi_code`` empty for some plans.

Names are deliberately **not** a fallback. Scheme-name matching across "Direct
Growth" / "Direct - Growth" / "Direct Plan Growth" is where silent mis-mapping
comes from, and a wrong link here would attach one fund's rating to another.
An unresolved plan is simply absent from the table, and every read path treats
absent as "no VR data", never as a default.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import delete, func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.vr_data.schema import SCHEME_LINK, table_for

logger = logging.getLogger(__name__)


@dataclass
class CrosswalkReport:
    by_isin: int = 0
    by_amfi_code: int = 0
    manual_preserved: int = 0
    total_links: int = 0
    our_schemes: int = 0
    vr_plans: int = 0

    @property
    def coverage_pct(self) -> float:
        """Share of *our* schemes that resolved. The number that matters —
        VR plans we cannot map are plans nobody holds; our schemes we cannot
        map are users seeing no VR data."""
        if not self.our_schemes:
            return 0.0
        return round(100.0 * self.total_links / self.our_schemes, 2)


async def rebuild_crosswalk(db: AsyncSession) -> CrosswalkReport:
    """Rebuild ``vr.scheme_link`` from the mirror. Idempotent.

    Manual links (``match_method='manual'``) survive a rebuild — they exist
    precisely because the automatic methods could not resolve that plan, and
    silently discarding them would undo the operator's work on every sync.
    """
    report = CrosswalkReport()

    manual = (
        (
            await db.execute(
                select(SCHEME_LINK).where(SCHEME_LINK.c.match_method == "manual")
            )
        )
        .mappings()
        .all()
    )
    report.manual_preserved = len(manual)
    manual_plan_ids = {row["plan_id"] for row in manual}

    await db.execute(delete(SCHEME_LINK).where(SCHEME_LINK.c.match_method != "manual"))

    # -- ISIN, the exact match --------------------------------------------
    subplan_isin = table_for("subplan_isin")
    isin_rows = (
        await db.execute(
            text(
                """
                SELECT DISTINCT ON (v.plan_id)
                       v.plan_id, m.scheme_code, v.isin_code AS isin
                  FROM vr.subplan_isin v
                  JOIN mf_fund_metadata m
                    ON upper(trim(m.isin)) = upper(trim(v.isin_code))
                 WHERE v.isin_code IS NOT NULL AND m.isin IS NOT NULL
                 ORDER BY v.plan_id, m.scheme_code
                """
            )
        )
    ).mappings().all()
    report.by_isin = await _insert_links(db, isin_rows, "isin", 1.0, manual_plan_ids)

    # -- AMFI code, for plans ISIN missed ---------------------------------
    amfi_rows = (
        await db.execute(
            text(
                """
                SELECT DISTINCT ON (v.plan_id)
                       v.plan_id, m.scheme_code, v.amfi_code, m.isin
                  FROM vr.fund_basic_details v
                  JOIN mf_fund_metadata m
                    ON trim(m.scheme_code) = trim(v.amfi_code)
                 WHERE v.amfi_code IS NOT NULL AND v.amfi_code <> ''
                   AND NOT EXISTS (
                         SELECT 1 FROM vr.scheme_link l WHERE l.plan_id = v.plan_id
                       )
                 ORDER BY v.plan_id, m.scheme_code
                """
            )
        )
    ).mappings().all()
    report.by_amfi_code = await _insert_links(
        db, amfi_rows, "amfi_code", 0.95, manual_plan_ids
    )

    await db.commit()

    report.total_links = int(
        (await db.execute(select(func.count()).select_from(SCHEME_LINK))).scalar() or 0
    )
    report.our_schemes = int(
        (
            await db.execute(
                text("SELECT count(*) FROM mf_fund_metadata WHERE is_active")
            )
        ).scalar()
        or 0
    )
    report.vr_plans = int(
        (
            await db.execute(
                select(func.count(func.distinct(subplan_isin.c.plan_id)))
            )
        ).scalar()
        or 0
    )
    logger.info(
        "vr crosswalk rebuilt: %d links (%d isin, %d amfi, %d manual) = %.2f%% of "
        "our %d active schemes",
        report.total_links,
        report.by_isin,
        report.by_amfi_code,
        report.manual_preserved,
        report.coverage_pct,
        report.our_schemes,
    )
    return report


async def _insert_links(
    db: AsyncSession,
    rows,
    method: str,
    confidence: float,
    skip_plan_ids: set[str],
) -> int:
    payload = [
        {
            "plan_id": row["plan_id"],
            "scheme_code": row.get("scheme_code"),
            "isin": row.get("isin"),
            "amfi_code": row.get("amfi_code"),
            "match_method": method,
            "confidence": confidence,
        }
        for row in rows
        if row["plan_id"] not in skip_plan_ids
    ]
    if not payload:
        return 0
    stmt = pg_insert(SCHEME_LINK).values(payload)
    await db.execute(stmt.on_conflict_do_nothing(index_elements=[SCHEME_LINK.c.plan_id]))
    return len(payload)


async def link_manually(
    db: AsyncSession, *, plan_id: str, scheme_code: str
) -> None:
    """Pin one mapping by hand. Survives every rebuild."""
    stmt = pg_insert(SCHEME_LINK).values(
        plan_id=plan_id,
        scheme_code=scheme_code,
        match_method="manual",
        confidence=1.0,
    )
    await db.execute(
        stmt.on_conflict_do_update(
            index_elements=[SCHEME_LINK.c.plan_id],
            set_={
                "scheme_code": scheme_code,
                "match_method": "manual",
                "confidence": 1.0,
            },
        )
    )
    await db.commit()


async def plan_id_for(db: AsyncSession, scheme_code: str) -> Optional[str]:
    return (
        await db.execute(
            select(SCHEME_LINK.c.plan_id).where(
                SCHEME_LINK.c.scheme_code == scheme_code
            )
        )
    ).scalar_one_or_none()


async def unresolved_schemes(db: AsyncSession, *, limit: int = 200) -> list[dict]:
    """Our active schemes with no VR link — the operator's worklist."""
    rows = (
        await db.execute(
            text(
                """
                SELECT m.scheme_code, m.scheme_name, m.isin
                  FROM mf_fund_metadata m
                 WHERE m.is_active
                   AND NOT EXISTS (
                         SELECT 1 FROM vr.scheme_link l
                          WHERE l.scheme_code = m.scheme_code
                       )
                 ORDER BY m.scheme_name
                 LIMIT :limit
                """
            ),
            {"limit": limit},
        )
    ).mappings().all()
    return [dict(r) for r in rows]
