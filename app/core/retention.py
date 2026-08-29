"""Retention policy: what we keep, for how long, and the job that enforces it.

Before this, nothing in the system was ever deleted. Chat transcripts, raw
identity copies lifted from statements, archived PDFs and staged screenshots all
accumulated indefinitely, which is a storage-limitation problem regardless of
how well the live data is protected — the oldest copy is the one nobody is
thinking about when a breach happens.

Prozpr is **not** SEBI-registered, so no statutory floor forces us to keep KYC or
transaction records. Every period below is therefore a product decision, not a
legal one, and legal should sign them off before this runs in production.

The registry is data, not code paths, so adding a table means adding a row here
rather than editing a job.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RetentionPolicy:
    """One rule. ``sql`` must be idempotent and scoped by ``:cutoff``."""

    name: str
    days: int
    rationale: str
    sql: str


#: Ordered children-before-parents where a rule has dependants.
POLICIES: tuple[RetentionPolicy, ...] = (
    RetentionPolicy(
        name="chat_ai_module_runs",
        days=730,
        rationale=(
            "Per-turn LLM input/output payloads. A second copy of everything in "
            "chat_messages, so it is the cheaper of the two to drop first."
        ),
        sql=(
            "DELETE FROM chat_ai_module_runs "
            "WHERE created_at IS NOT NULL AND created_at < :cutoff"
        ),
    ),
    RetentionPolicy(
        name="chat_messages",
        days=730,
        rationale=(
            "Verbatim conversation. People state salary, family circumstances "
            "and plans here in their own words; there is no redaction and no "
            "way to know in advance what a message contains."
        ),
        sql=(
            "DELETE FROM chat_messages "
            "WHERE created_at IS NOT NULL AND created_at < :cutoff"
        ),
    ),
    RetentionPolicy(
        name="mf_aa_imports_identity",
        days=90,
        rationale=(
            "Data MINIMISATION, not expiry: the statement header keeps a second "
            "verbatim copy of the investor's name, address, email, mobile and "
            "PAN. Once the import is normalised, nothing downstream reads those "
            "columns again — so they are nulled rather than the row dropped, "
            "which would take the audit trail with it."
        ),
        sql=(
            "UPDATE mf_aa_imports SET "
            "pan = NULL, pekrn = NULL, email = NULL, mobile = NULL, "
            "investor_first_name = NULL, investor_middle_name = NULL, "
            "investor_last_name = NULL, address_line_1 = NULL, "
            "address_line_2 = NULL, address_line_3 = NULL, city = NULL, "
            "district = NULL, state = NULL, pincode = NULL "
            "WHERE normalized_at IS NOT NULL AND normalized_at < :cutoff "
            "AND (pan IS NOT NULL OR email IS NOT NULL OR address_line_1 IS NOT NULL)"
        ),
    ),
    RetentionPolicy(
        name="consent_records_superseded",
        days=2555,
        rationale=(
            "Kept far longer than anything else, on purpose: this is the "
            "evidence that consent was obtained. Seven years so a record "
            "outlives any dispute about it."
        ),
        sql=(
            "DELETE FROM consent_records "
            "WHERE recorded_at IS NOT NULL AND recorded_at < :cutoff"
        ),
    ),
    RetentionPolicy(
        name="grievances_resolved",
        days=1095,
        rationale="Closed complaints, kept three years for audit.",
        sql=(
            "DELETE FROM grievances "
            "WHERE status = 'resolved' AND resolved_at IS NOT NULL "
            "AND resolved_at < :cutoff"
        ),
    ),
)


def _cutoff(days: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days)


async def apply_retention(
    db: AsyncSession, *, dry_run: bool = True
) -> dict[str, int]:
    """Run every policy. Returns rows affected per policy.

    Defaults to a dry run: the first thing anyone should do with a destructive
    scheduled job is look at what it *would* delete.
    """
    results: dict[str, int] = {}
    for policy in POLICIES:
        cutoff = _cutoff(policy.days)
        try:
            if dry_run:
                count_sql = _to_count(policy.sql)
                n = (
                    await db.execute(text(count_sql), {"cutoff": cutoff})
                ).scalar_one_or_none() or 0
            else:
                n = (await db.execute(text(policy.sql), {"cutoff": cutoff})).rowcount or 0
            results[policy.name] = int(n)
        except Exception:
            # A missing table on an older database must not stop the rest.
            logger.exception("Retention policy %s failed; continuing.", policy.name)
            results[policy.name] = -1
    if not dry_run:
        await db.commit()
    logger.info("Retention pass (%s): %s", "dry-run" if dry_run else "applied", results)
    return results


def _to_count(sql: str) -> str:
    """Turn a policy statement into the equivalent COUNT for a dry run."""
    upper = sql.upper()
    if upper.startswith("DELETE FROM"):
        rest = sql[len("DELETE FROM") :].lstrip()
        table, _, where = rest.partition(" WHERE ")
        return f"SELECT COUNT(*) FROM {table.strip()} WHERE {where}"
    if upper.startswith("UPDATE"):
        rest = sql[len("UPDATE") :].lstrip()
        table = rest.split(" SET ", 1)[0].strip()
        where = sql.split(" WHERE ", 1)[1]
        return f"SELECT COUNT(*) FROM {table} WHERE {where}"
    raise ValueError(f"Unsupported policy statement: {sql[:40]}")
