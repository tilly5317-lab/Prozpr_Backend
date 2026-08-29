"""One-off rollout step: give every user's pre-existing data a CAS snapshot.

Before snapshot versioning, a CAMS re-upload deleted everything derived from the
previous statement. Now it supersedes instead, and every derived row carries the
``cas_upload_id`` of the statement it came from. Rows written before the feature
shipped carry NULL, which means "not owned by any statement" and stays visible
under every scope — safe, but it also means those rows would be counted ALONGSIDE
the next statement the user uploads.

This script closes that window ahead of time: for each user holding unstamped
data it mints one ``legacy`` snapshot, marks it active, and stamps their rows
with it. The very next upload then supersedes it cleanly.

Running it is not strictly required — ``ingest_cams_pdf`` performs the same
adoption for a user who has data but no active snapshot, so the system heals
itself one upload at a time. Running it up front just means no user is ever in
the mixed state, and the numbers are visible before anyone uploads anything.

Users who ALREADY have a snapshot are skipped and reported: adopting stray NULL
rows into an existing active snapshot could merge two statements' data into one,
which is exactly what this whole feature exists to prevent.

Usage (from Prozpr_Backend, venv python):
    python scripts/backfill_cas_uploads.py            # dry run (default)
    python scripts/backfill_cas_uploads.py --apply    # write changes

Note: needs the ``cas_upload_id`` columns to exist. They are created by
``apply_postgres_schema_patches()`` on boot; if ``SKIP_STARTUP_DB_DDL`` is set
on this environment, run that first.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
import uuid
from pathlib import Path

import asyncpg

# Tables stamped with a snapshot id, and how each row reaches its user. Kept in
# step with app/domains/ingestion/services/cas_upload_service.py.
BY_USER_ID: tuple[str, ...] = (
    "mf_aa_imports",
    "mf_transactions",
    "mf_sip_mandates",
    "user_mf_latest_snapshot",
    "portfolio_allocation_snapshots",
    "funds",
    "user_investment_lists",
    "portfolio_networth_jobs",
    "user_portfolio_nav_history",
    "asset_allocation_runs",
    "practical_asset_allocation_runs",
    "rebalancing_runs",
    "additional_investment_runs",
    "cashflow_plan_runs",
    "chat_ai_module_runs",
)
BY_PORTFOLIO: tuple[str, ...] = (
    "portfolio_holdings",
    "portfolio_allocations",
    "portfolio_history",
)


def _dsn() -> str:
    env_path = Path(__file__).resolve().parents[1] / ".env"
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("DATABASE_URL="):
            url = line.split("=", 1)[1].strip()
            url = url.replace("postgresql+asyncpg://", "postgresql://")
            return re.sub(r"\?ssl=require$", "", url)
    raise SystemExit("DATABASE_URL not found in .env")


async def _users_with_unstamped_data(conn: asyncpg.Connection) -> list[uuid.UUID]:
    """Users holding at least one unstamped row in any snapshot-owned table."""
    parts = [
        f"SELECT DISTINCT user_id FROM {t} WHERE cas_upload_id IS NULL AND user_id IS NOT NULL"
        for t in BY_USER_ID
    ]
    parts += [
        f"SELECT DISTINCT p.user_id FROM {t} c JOIN portfolios p ON c.portfolio_id = p.id "
        f"WHERE c.cas_upload_id IS NULL"
        for t in BY_PORTFOLIO
    ]
    rows = await conn.fetch(" UNION ".join(parts))
    return [r["user_id"] for r in rows if r["user_id"] is not None]


async def _adopt(
    conn: asyncpg.Connection, user_id: uuid.UUID, cas_upload_id: uuid.UUID
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table in BY_USER_ID:
        tag = await conn.execute(
            f"UPDATE {table} SET cas_upload_id = $1 "
            f"WHERE user_id = $2 AND cas_upload_id IS NULL",
            cas_upload_id,
            user_id,
        )
        n = int(tag.rsplit(" ", 1)[-1])
        if n:
            counts[table] = n
    for table in BY_PORTFOLIO:
        tag = await conn.execute(
            f"UPDATE {table} SET cas_upload_id = $1 "
            f"WHERE cas_upload_id IS NULL AND portfolio_id IN "
            f"(SELECT id FROM portfolios WHERE user_id = $2)",
            cas_upload_id,
            user_id,
        )
        n = int(tag.rsplit(" ", 1)[-1])
        if n:
            counts[table] = n
    return counts


async def main(apply: bool) -> int:
    conn = await asyncpg.connect(_dsn())
    try:
        users = await _users_with_unstamped_data(conn)
        print(f"users with unstamped data: {len(users)}")
        adopted_users = 0
        skipped: list[uuid.UUID] = []
        total_rows = 0

        for user_id in users:
            existing = await conn.fetchval(
                "SELECT count(*) FROM cas_uploads WHERE user_id = $1", user_id
            )
            if existing:
                skipped.append(user_id)
                continue

            snapshot_id = uuid.uuid4()
            if apply:
                async with conn.transaction():
                    await conn.execute(
                        """
                        INSERT INTO cas_uploads
                          (id, user_id, seq, status, source_filename, activated_at)
                        VALUES ($1, $2, 1, 'active', '(pre-versioning data)', now())
                        """,
                        snapshot_id,
                        user_id,
                    )
                    counts = await _adopt(conn, user_id, snapshot_id)
            else:
                # Dry run: count what WOULD be adopted, touching nothing.
                counts = {}
                for table in BY_USER_ID:
                    n = await conn.fetchval(
                        f"SELECT count(*) FROM {table} "
                        f"WHERE user_id = $1 AND cas_upload_id IS NULL",
                        user_id,
                    )
                    if n:
                        counts[table] = n
                for table in BY_PORTFOLIO:
                    n = await conn.fetchval(
                        f"SELECT count(*) FROM {table} c "
                        f"JOIN portfolios p ON c.portfolio_id = p.id "
                        f"WHERE p.user_id = $1 AND c.cas_upload_id IS NULL",
                        user_id,
                    )
                    if n:
                        counts[table] = n

            adopted_users += 1
            total_rows += sum(counts.values())
            print(f"  {user_id}: {sum(counts.values())} rows {counts}")

        verb = "adopted" if apply else "would adopt"
        print(f"\n{verb} {total_rows} rows for {adopted_users} users")
        if skipped:
            print(
                f"skipped {len(skipped)} users who already have a cas_uploads row "
                f"(their stray rows are adopted by the next ingest): "
                f"{', '.join(str(u) for u in skipped[:10])}"
                + (" …" if len(skipped) > 10 else "")
            )
        if not apply:
            print("\nDRY RUN — re-run with --apply to write.")
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true", help="write changes (default: dry run)"
    )
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.apply)))
