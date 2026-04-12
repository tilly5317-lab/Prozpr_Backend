"""Add funds.user_id (+ index + FK) if missing — matches migration d2e91b8f7a11.

Use when Alembic is out of sync but the ORM expects user_id (discovery queries).

  python scripts/ensure_funds_user_id_column.py
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
        r = await conn.execute(
            text(
                """
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'funds' AND column_name = 'user_id'
                """
            )
        )
        if r.scalar_one_or_none() is not None:
            print("OK: funds.user_id already exists.")
        else:
            await conn.execute(text("ALTER TABLE funds ADD COLUMN user_id UUID"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_funds_user_id ON funds (user_id)"))
            r2 = await conn.execute(
                text("SELECT 1 FROM pg_constraint WHERE conname = 'fk_funds_user_id_users'")
            )
            if r2.scalar_one_or_none() is None:
                await conn.execute(
                    text(
                        """
                        ALTER TABLE funds ADD CONSTRAINT fk_funds_user_id_users
                        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                        """
                    )
                )
            print("OK: added funds.user_id, index, and foreign key.")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
