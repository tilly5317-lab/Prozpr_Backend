"""Walk every row in the database that belongs to one user.

Both the access right and the erasure right need the same answer to the same
question — "what rows are this person's?" — so they share this walk rather than
each keeping a table list that drifts.

**Why a graph walk and not a hand-written list.** Nothing cascades from
``users``: 47 foreign keys reference it and the ones that actually hold rows are
``NO ACTION``, so ``DELETE FROM users`` aborts on the first constraint every
time. A hand-ordered delete list is worse than that — it silently misses
grandchildren. Counting only direct ``user_id`` foreign keys once gave ~61.5k
rows for 44 accounts; walking the graph properly found 43 tables once
``rebalancing_fund_rows``, ``mf_aa_transactions``, ``portfolio_holdings``,
``chat_messages`` and friends were included.

The walk is keyed on ``(table, column, values)`` rather than on primary keys, so
no table needs a known PK and a table reachable by two paths is simply visited
twice — deletes are idempotent, and only the *counts* double up, which is why a
dry run reports a higher total than the real thing.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

#: Depth cap. The real graph is ~4 deep; this only stops a cycle from spinning.
_MAX_DEPTH = 12

#: Tables that must NOT be walked as user data.
#: ``deleted_user_tombstones`` is the record that the erasure happened — purging
#: it along with everything else would destroy the evidence and let a backup
#: restore quietly bring the account back.
_NEVER_WALK = frozenset({"deleted_user_tombstones", "alembic_version"})

_FK_SQL = text(
    """
    SELECT
        src_ns.nspname   AS src_schema,
        src.relname      AS src_table,
        src_col.attname  AS src_column,
        tgt.relname      AS tgt_table,
        tgt_col.attname  AS tgt_column
    FROM pg_constraint c
    JOIN pg_class src        ON src.oid = c.conrelid
    JOIN pg_namespace src_ns ON src_ns.oid = src.relnamespace
    JOIN pg_class tgt        ON tgt.oid = c.confrelid
    JOIN unnest(c.conkey)  WITH ORDINALITY AS k(attnum, ord) ON TRUE
    JOIN unnest(c.confkey) WITH ORDINALITY AS fk(attnum, ord) ON fk.ord = k.ord
    JOIN pg_attribute src_col ON src_col.attrelid = c.conrelid AND src_col.attnum = k.attnum
    JOIN pg_attribute tgt_col ON tgt_col.attrelid = c.confrelid AND tgt_col.attnum = fk.attnum
    WHERE c.contype = 'f' AND src_ns.nspname = 'public'
    """
)


async def load_fk_graph(db: AsyncSession) -> dict[str, list[dict[str, str]]]:
    """``{referenced_table: [{src_table, src_column, tgt_column}, ...]}``.

    Read from ``pg_constraint`` at runtime so a newly added table is covered the
    moment it exists — the failure mode of a checked-in list is that it is
    correct on the day it is written and wrong forever after.
    """
    graph: dict[str, list[dict[str, str]]] = {}
    for row in (await db.execute(_FK_SQL)).mappings():
        if row["src_table"] in _NEVER_WALK:
            continue
        graph.setdefault(row["tgt_table"], []).append(
            {
                "src_table": row["src_table"],
                "src_column": row["src_column"],
                "tgt_column": row["tgt_column"],
            }
        )
    return graph


async def collect_user_rows(
    db: AsyncSession, user_id: uuid.UUID, *, limit_per_table: int | None = None
) -> dict[str, list[dict[str, Any]]]:
    """Every row belonging to ``user_id``, keyed by table name.

    Used by the export. ``limit_per_table`` caps the daily series tables — a
    single account's ``user_portfolio_nav_history`` runs to ~1.2k rows.
    """
    graph = await load_fk_graph(db)
    out: dict[str, list[dict[str, Any]]] = {}

    async def visit(table: str, column: str, values: list[Any], depth: int) -> None:
        if depth > _MAX_DEPTH or not values or table in _NEVER_WALK:
            return
        cap = f" LIMIT {int(limit_per_table)}" if limit_per_table else ""
        rows = (
            await db.execute(
                text(f'SELECT * FROM "{table}" WHERE "{column}" = ANY(:vals){cap}'),
                {"vals": values},
            )
        ).mappings()
        rows = [dict(r) for r in rows]
        if not rows:
            return
        out.setdefault(table, []).extend(rows)

        for child in graph.get(table, []):
            key = child["tgt_column"]
            parent_values = [r[key] for r in rows if r.get(key) is not None]
            if parent_values:
                await visit(
                    child["src_table"],
                    child["src_column"],
                    list({*parent_values}),
                    depth + 1,
                )

    await visit("users", "id", [user_id], 0)
    return out


async def delete_user_rows(
    db: AsyncSession, user_id: uuid.UUID, *, dry_run: bool = True
) -> dict[str, int]:
    """Depth-first delete of everything belonging to ``user_id``.

    Children are deleted before parents, which is what makes this work against a
    schema whose foreign keys are ``NO ACTION``. Does NOT commit — the caller
    owns the transaction, so the purge and its tombstone land together or not at
    all.
    """
    graph = await load_fk_graph(db)
    counts: dict[str, int] = {}

    async def visit(table: str, column: str, values: list[Any], depth: int) -> None:
        if depth > _MAX_DEPTH or not values or table in _NEVER_WALK:
            return
        rows = [
            dict(r)
            for r in (
                await db.execute(
                    text(f'SELECT * FROM "{table}" WHERE "{column}" = ANY(:vals)'),
                    {"vals": values},
                )
            ).mappings()
        ]
        if not rows:
            return

        # Depth-first: every child must be gone before this table's rows are.
        for child in graph.get(table, []):
            key = child["tgt_column"]
            parent_values = [r[key] for r in rows if r.get(key) is not None]
            if parent_values:
                await visit(
                    child["src_table"],
                    child["src_column"],
                    list({*parent_values}),
                    depth + 1,
                )

        counts[table] = counts.get(table, 0) + len(rows)
        if not dry_run:
            await db.execute(
                text(f'DELETE FROM "{table}" WHERE "{column}" = ANY(:vals)'),
                {"vals": values},
            )

    await visit("users", "id", [user_id], 0)
    return counts
