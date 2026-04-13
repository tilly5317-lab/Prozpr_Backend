import asyncio
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.database import _get_engine
from sqlalchemy import text

async def main():
    e = _get_engine()
    try:
        async with e.connect() as c:
            r = await c.execute(text("SELECT 1"))
            print("DB OK:", r.scalar())
    except Exception as ex:
        print(f"DB FAIL: {ex}")
    finally:
        await e.dispose()

asyncio.run(main())
