"""One-shot bulk loader: replace mf_nav_history with rows from a TSV file.

File format (tab-separated, header row required):
    scheme_code  isin_growth  isin_div_reinvestment  date  nav

Strategy:
1. TRUNCATE mf_nav_history.
2. COPY the file into a TEMP staging table (whole upload in one stream).
3. INSERT into mf_nav_history joining mf_fund_metadata for NOT NULL
   columns (scheme_name, mf_type), with ON CONFLICT upsert on
   (scheme_code, nav_date). Rows whose scheme_code is missing from
   mf_fund_metadata are skipped (FK would reject them).

Live progress: bytes/percent/MB-per-second/ETA printed every 1s during COPY.
"""

from __future__ import annotations

import os
import sys
import time
from urllib.parse import urlparse

import psycopg2

try:
    from dotenv import load_dotenv  # type: ignore[import-not-found]
    load_dotenv(
        os.path.join(os.path.dirname(__file__), os.pardir, ".env"),
        encoding="utf-8-sig",
    )
except ImportError:
    pass

FILE = r"C:\Users\Lenovo\Desktop\Intern\mf_nav_history.txt"


def _dsn_from_env() -> str:
    raw = os.environ["DATABASE_URL"]
    raw = raw.replace("postgresql+asyncpg://", "postgresql://")
    if "ssl=require" in raw and "sslmode=" not in raw:
        raw = raw.replace("ssl=require", "sslmode=require")
    return raw


class ProgressFile:
    """File-like wrapper that prints byte/percent/MB-s/ETA on each read."""

    def __init__(self, path: str, total: int) -> None:
        self._f = open(path, "rb")
        self._total = total
        self._read = 0
        self._t0 = time.monotonic()
        self._last = 0.0

    def read(self, size: int = -1) -> bytes:
        chunk = self._f.read(size)
        self._read += len(chunk)
        now = time.monotonic()
        if now - self._last >= 1.0 or not chunk:
            self._tick(now, final=not chunk)
            self._last = now
        return chunk

    def readline(self, size: int = -1) -> bytes:
        line = self._f.readline(size)
        self._read += len(line)
        now = time.monotonic()
        if now - self._last >= 1.0 or not line:
            self._tick(now, final=not line)
            self._last = now
        return line

    def _tick(self, now: float, final: bool) -> None:
        elapsed = max(now - self._t0, 1e-6)
        mb = self._read / 1024 / 1024
        total_mb = self._total / 1024 / 1024
        pct = (self._read / self._total * 100) if self._total else 100.0
        rate = mb / elapsed
        if rate > 0 and self._read < self._total:
            eta = (self._total - self._read) / 1024 / 1024 / rate
            eta_s = f"{eta:6.0f}s"
        else:
            eta_s = "   --- "
        print(
            f"[copy] {pct:6.2f}% | {mb:8.1f}/{total_mb:.1f} MB | "
            f"{rate:5.2f} MB/s | ETA {eta_s} | elapsed {elapsed:5.0f}s",
            flush=True,
        )

    def close(self) -> None:
        self._f.close()


def main() -> None:
    if not os.path.exists(FILE):
        print(f"ERR: file not found: {FILE}", file=sys.stderr)
        sys.exit(2)

    total_bytes = os.path.getsize(FILE)
    print(f"file: {FILE}")
    print(f"size: {total_bytes/1024/1024:.1f} MB")

    dsn = _dsn_from_env()
    host = urlparse(dsn).hostname
    print(f"db host: {host}")

    conn = psycopg2.connect(dsn)
    conn.autocommit = False
    cur = conn.cursor()
    print("connected.")

    try:
        # Confirm metadata coverage before doing anything destructive.
        cur.execute("SELECT COUNT(*) FROM mf_fund_metadata")
        meta_count = cur.fetchone()[0]
        print(f"mf_fund_metadata rows: {meta_count}")
        if meta_count == 0:
            print(
                "ABORT: mf_fund_metadata is empty; FK would reject every row. "
                "Populate metadata first (e.g. mfapi universe ingest).",
                file=sys.stderr,
            )
            sys.exit(3)

        cur.execute("SELECT COUNT(*) FROM mf_nav_history")
        old_count = cur.fetchone()[0]
        print(f"existing mf_nav_history rows: {old_count}")

        print("step 1/4: truncating mf_nav_history...")
        t0 = time.monotonic()
        cur.execute("TRUNCATE mf_nav_history")
        print(f"   ok ({time.monotonic()-t0:.1f}s)")

        print("step 2/4: creating staging temp table...")
        cur.execute(
            "CREATE TEMP TABLE _stg_nav ("
            "  scheme_code text, isin_growth text, isin_div text,"
            "  nav_date_raw text, nav_raw text"
            ") ON COMMIT DROP"
        )

        print("step 3/4: streaming COPY (live progress below)...")
        pf = ProgressFile(FILE, total_bytes)
        t0 = time.monotonic()
        cur.copy_expert(
            "COPY _stg_nav (scheme_code, isin_growth, isin_div, nav_date_raw, nav_raw) "
            "FROM STDIN WITH (FORMAT text, DELIMITER E'\\t', HEADER true, NULL '', ENCODING 'UTF8')",
            pf,
        )
        pf.close()
        copy_secs = time.monotonic() - t0
        cur.execute("SELECT COUNT(*) FROM _stg_nav")
        stg_count = cur.fetchone()[0]
        print(f"   ok: {stg_count} rows streamed in {copy_secs:.1f}s")

        print("step 4/4: merging into mf_nav_history (server-side join + upsert)...")
        t0 = time.monotonic()
        cur.execute(
            "INSERT INTO mf_nav_history "
            "  (id, scheme_code, isin, scheme_name, mf_type, nav, nav_date) "
            "SELECT gen_random_uuid(), "
            "       s.scheme_code, "
            "       NULLIF(COALESCE(NULLIF(s.isin_growth,''), s.isin_div), ''), "
            "       m.scheme_name, "
            "       COALESCE(NULLIF(m.category,''), 'Unknown'), "
            "       NULLIF(s.nav_raw,'')::numeric, "
            "       to_date(s.nav_date_raw, 'DD-MM-YYYY') "
            "FROM _stg_nav s "
            "JOIN mf_fund_metadata m ON m.scheme_code = s.scheme_code "
            "WHERE s.nav_raw IS NOT NULL AND s.nav_raw <> '' "
            "  AND s.nav_date_raw IS NOT NULL AND s.nav_date_raw <> '' "
            "ON CONFLICT (scheme_code, nav_date) DO UPDATE SET "
            "  nav = EXCLUDED.nav, "
            "  isin = COALESCE(EXCLUDED.isin, mf_nav_history.isin), "
            "  scheme_name = EXCLUDED.scheme_name, "
            "  mf_type = EXCLUDED.mf_type"
        )
        merged = cur.rowcount
        merge_secs = time.monotonic() - t0
        skipped = stg_count - merged
        print(
            f"   ok: {merged} rows inserted/updated in {merge_secs:.1f}s "
            f"(skipped {skipped} rows w/o matching mf_fund_metadata)"
        )

        print("committing...")
        conn.commit()
        cur.execute("SELECT COUNT(*) FROM mf_nav_history")
        new_count = cur.fetchone()[0]
        print(f"final mf_nav_history rows: {new_count}")
        print("DONE.")
    except Exception:
        conn.rollback()
        print("ROLLED BACK", file=sys.stderr)
        raise
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
