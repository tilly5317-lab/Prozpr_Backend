"""Add MEDIUM to PostgreSQL goal_priority_enum_v2 if it is missing.

Run from repo backend root:
  python scripts/ensure_goal_priority_medium_enum.py
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


def _db_url() -> str:
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    url = os.getenv("DATABASE_URL", "")
    if url.startswith("postgresql://") and "asyncpg" not in url:
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


async def main() -> None:
    url = _db_url()
    if not url or "postgresql" not in url:
        raise SystemExit("Set DATABASE_URL to your PostgreSQL URL in backend/.env")
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(text("ALTER TYPE goal_priority_enum_v2 ADD VALUE IF NOT EXISTS 'MEDIUM'"))
    await engine.dispose()
    print("OK: goal_priority_enum_v2 now includes MEDIUM (if it was missing).")


if __name__ == "__main__":
    asyncio.run(main())
