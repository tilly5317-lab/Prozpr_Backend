"""Run the asset allocation migration SQL using the app's raw asyncpg connection."""
import asyncio
from sqlalchemy import text
from app.database import _get_engine


engine = _get_engine()


async def main():
    with open("migrations/sql/add_asset_allocation_columns_and_tables.sql") as f:
        sql = f.read()

    async with engine.connect() as conn:
        raw = await conn.get_raw_connection()
        driver_conn = raw.driver_connection
        try:
            await driver_conn.execute(sql)
            print("Migration completed successfully!")
        except Exception as e:
            print(f"Migration failed: {e}")
            await engine.dispose()
            return

    # Verify
    async with engine.connect() as conn:
        rows = (await conn.execute(text(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' "
            "AND (table_name LIKE '%allocation%' OR table_name = 'goals') "
            "ORDER BY table_name"
        ))).fetchall()
        print("\nTables after migration:")
        for r in rows:
            print(f"  {r[0]}")

        cols = (await conn.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'goals' ORDER BY ordinal_position"
        ))).fetchall()
        print("\ngoals columns after migration:")
        for c in cols:
            print(f"  {c[0]}")

    await engine.dispose()


asyncio.run(main())
