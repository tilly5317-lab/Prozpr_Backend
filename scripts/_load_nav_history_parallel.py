"""Parallel + chunked + retry-able + RESUMABLE bulk loader for mf_nav_history.

Survives across script restarts: each chunk's COPY is committed in the same
transaction as a row in `_stg_nav_chunks` (the manifest). On restart, the
loader queries the manifest and skips chunks already done. Network outages,
crashes, or kill -9 lose at most the in-flight chunks (whose transactions
roll back server-side).

Tables created (and reused across runs):
  _stg_nav_load    — raw staging rows
  _stg_nav_chunks  — manifest: which chunk byte-ranges have committed
  _stg_nav_meta    — file_size fingerprint to detect file changes

Workflow:
  1. CREATE TABLE IF NOT EXISTS the three tables.
  2. If file_size in _stg_nav_meta differs from current file → reset all staging
     (treat as a new load).
  3. Plan chunks; subtract those already in _stg_nav_chunks; queue the rest.
  4. 8 workers upload remaining chunks; each commits its data + manifest atomically.
  5. After all chunks complete (this run + previously committed), do
     TRUNCATE mf_nav_history + INSERT FROM staging in one transaction, then
     drop the three staging tables.

To force a complete restart, set FORCE_FRESH=True or run with --fresh.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
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
N_WORKERS = 8
CHUNK_TARGET_MB = 7
MAX_RETRIES = 12
RETRY_BACKOFF_CAP_S = 60
STAGING = "_stg_nav_load"
MANIFEST = "_stg_nav_chunks"
META = "_stg_nav_meta"

_lock = threading.Lock()
_uploaded = 0
_total = 0
_t0 = 0.0


def _dsn() -> str:
    raw = os.environ["DATABASE_URL"]
    raw = raw.replace("postgresql+asyncpg://", "postgresql://")
    if "ssl=require" in raw and "sslmode=" not in raw:
        raw = raw.replace("ssl=require", "sslmode=require")
    return raw


def _connect(dsn: str):
    return psycopg2.connect(
        dsn,
        keepalives=1,
        keepalives_idle=20,
        keepalives_interval=5,
        keepalives_count=5,
        connect_timeout=15,
    )


class BoundedReader:
    def __init__(self, path: str, start: int, end: int) -> None:
        self._f = open(path, "rb")
        self._f.seek(start)
        self._end = end

    def _bump(self, n: int) -> None:
        global _uploaded
        with _lock:
            _uploaded += n

    def read(self, size: int = -1) -> bytes:
        rem = self._end - self._f.tell()
        if rem <= 0:
            return b""
        if size < 0 or size > rem:
            size = rem
        chunk = self._f.read(size)
        self._bump(len(chunk))
        return chunk

    def readline(self, size: int = -1) -> bytes:
        rem = self._end - self._f.tell()
        if rem <= 0:
            return b""
        if size < 0 or size > rem:
            size = rem
        line = self._f.readline(size)
        self._bump(len(line))
        return line

    def close(self) -> None:
        self._f.close()


def _printer(stop: threading.Event, chunk_state: dict) -> None:
    while not stop.wait(2.0):
        with _lock:
            up = _uploaded
        elapsed = max(time.monotonic() - _t0, 1e-6)
        mb = up / 1024 / 1024
        total_mb = _total / 1024 / 1024
        pct = (up / _total * 100) if _total else 0.0
        rate = mb / elapsed
        if rate > 0 and up < _total:
            eta = (_total - up) / 1024 / 1024 / rate
            eta_s = f"{eta:6.0f}s"
        else:
            eta_s = "   --- "
        with chunk_state["lock"]:
            done = chunk_state["done"]
            total_chunks = chunk_state["total"]
            retries = chunk_state["retries"]
            skipped = chunk_state["skipped"]
        print(
            f"[copy] {pct:6.2f}% | {mb:7.1f}/{total_mb:.1f} MB | "
            f"{rate:5.2f} MB/s | ETA {eta_s} | chunks {done}/{total_chunks} "
            f"(skipped {skipped}) | retries {retries} | elapsed {elapsed:5.0f}s",
            flush=True,
        )


def _plan_chunks(path: str, target_bytes: int) -> list[tuple[int, int]]:
    size = os.path.getsize(path)
    chunks: list[tuple[int, int]] = []
    with open(path, "rb") as f:
        f.readline()
        cursor = f.tell()
        while cursor < size:
            target = min(cursor + target_bytes, size)
            f.seek(target)
            f.readline()
            end = f.tell() if target < size else size
            chunks.append((cursor, end))
            cursor = end
    return chunks


def _bootstrap(dsn: str, total_bytes: int, fresh: bool) -> tuple[set[int], dict[int, int]]:
    """Set up staging tables; detect resumable state.

    Returns:
        (done_chunk_indices, byte_credits_per_chunk_idx).
    """
    conn = _connect(dsn)
    conn.autocommit = True
    try:
        with conn.cursor() as c:
            c.execute(
                f"CREATE TABLE IF NOT EXISTS {STAGING} ("
                "  scheme_code text, isin_growth text, isin_div text,"
                "  nav_date_raw text, nav_raw text)"
            )
            c.execute(
                f"CREATE TABLE IF NOT EXISTS {MANIFEST} ("
                "  chunk_idx int PRIMARY KEY,"
                "  byte_start bigint NOT NULL,"
                "  byte_end bigint NOT NULL,"
                "  completed_at timestamptz NOT NULL DEFAULT now())"
            )
            c.execute(
                f"CREATE TABLE IF NOT EXISTS {META} ("
                "  key text PRIMARY KEY, value text NOT NULL)"
            )
            c.execute(f"SELECT value FROM {META} WHERE key = 'file_size'")
            row = c.fetchone()
            existing_size = int(row[0]) if row else None

            do_reset = False
            if fresh:
                print("   --fresh: resetting staging tables")
                do_reset = True
            elif existing_size is not None and existing_size != total_bytes:
                print(
                    f"   file size changed ({existing_size} -> {total_bytes}); "
                    "resetting staging tables"
                )
                do_reset = True

            if do_reset:
                c.execute(f"TRUNCATE {STAGING}")
                c.execute(f"TRUNCATE {MANIFEST}")
                c.execute(f"DELETE FROM {META}")

            c.execute(
                f"INSERT INTO {META} (key, value) VALUES ('file_size', %s) "
                "ON CONFLICT (key) DO NOTHING",
                (str(total_bytes),),
            )

            c.execute(f"SELECT chunk_idx, byte_start, byte_end FROM {MANIFEST}")
            rows = c.fetchall()
            done = {r[0] for r in rows}
            credits = {r[0]: int(r[2]) - int(r[1]) for r in rows}
            print(
                f"   resumable state: {len(done)} chunks already committed "
                f"({sum(credits.values())/1024/1024:.1f} MB)"
            )

            c.execute("SELECT COUNT(*) FROM mf_fund_metadata")
            print(f"   mf_fund_metadata rows: {c.fetchone()[0]}")
            c.execute("SELECT COUNT(*) FROM mf_nav_history")
            print(f"   existing mf_nav_history rows: {c.fetchone()[0]}")
            return done, credits
    finally:
        conn.close()


def _do_chunk(
    idx: int,
    start: int,
    end: int,
    dsn: str,
    chunk_state: dict,
) -> tuple[int, str]:
    last_err = ""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            conn = _connect(dsn)
            conn.autocommit = False
            try:
                cur = conn.cursor()
                rd = BoundedReader(FILE, start, end)
                try:
                    cur.copy_expert(
                        f"COPY {STAGING} (scheme_code, isin_growth, isin_div, "
                        f"nav_date_raw, nav_raw) FROM STDIN WITH "
                        f"(FORMAT text, DELIMITER E'\\t', NULL '', ENCODING 'UTF8')",
                        rd,
                    )
                finally:
                    rd.close()
                # Manifest insert in the SAME transaction = atomic with the data.
                cur.execute(
                    f"INSERT INTO {MANIFEST} (chunk_idx, byte_start, byte_end) "
                    "VALUES (%s, %s, %s) ON CONFLICT (chunk_idx) DO NOTHING",
                    (idx, start, end),
                )
                conn.commit()
                return idx, "ok"
            except Exception as exc:
                last_err = f"attempt {attempt}: {exc}"
                try:
                    conn.rollback()
                except Exception:
                    pass
            finally:
                try:
                    conn.close()
                except Exception:
                    pass
        except Exception as exc:
            last_err = f"attempt {attempt} (connect): {exc}"
        with chunk_state["lock"]:
            chunk_state["retries"] += 1
        time.sleep(min(2 ** (attempt - 1), RETRY_BACKOFF_CAP_S))
    return idx, f"FAIL after {MAX_RETRIES}: {last_err}"


def main() -> None:
    global _total, _t0, _uploaded
    fresh = "--fresh" in sys.argv
    if not os.path.exists(FILE):
        print(f"ERR: file not found: {FILE}", file=sys.stderr)
        sys.exit(2)
    _total = os.path.getsize(FILE)
    print(f"file: {FILE}")
    print(f"size: {_total/1024/1024:.1f} MB")
    dsn = _dsn()
    print(f"db host: {urlparse(dsn).hostname}")
    print(
        f"workers: {N_WORKERS} | chunk target: {CHUNK_TARGET_MB} MB | "
        f"retries: {MAX_RETRIES} | backoff cap: {RETRY_BACKOFF_CAP_S}s"
    )

    print("setup: staging tables...")
    done_chunks, byte_credits = _bootstrap(dsn, _total, fresh)

    print("setup: planning chunks...")
    chunks = _plan_chunks(FILE, CHUNK_TARGET_MB * 1024 * 1024)
    print(f"   total chunks: {len(chunks)} | already done: {len(done_chunks)}")
    pending = [(i, s, e) for i, (s, e) in enumerate(chunks) if i not in done_chunks]
    print(f"   pending: {len(pending)}")

    if not pending:
        print("upload: nothing to upload — all chunks already committed.")
    else:
        # Pre-credit bytes for chunks already done so progress reflects total work.
        with _lock:
            _uploaded = sum(byte_credits.values())

        chunk_state = {
            "lock": threading.Lock(),
            "done": len(done_chunks),
            "total": len(chunks),
            "retries": 0,
            "skipped": len(done_chunks),
        }

        print(f"upload: launching {N_WORKERS} parallel workers on {len(pending)} chunks...")
        _t0 = time.monotonic()
        stop = threading.Event()
        printer = threading.Thread(target=_printer, args=(stop, chunk_state), daemon=True)
        printer.start()

        failures: list[tuple[int, str]] = []
        with ThreadPoolExecutor(max_workers=N_WORKERS) as ex:
            futs = [ex.submit(_do_chunk, i, s, e, dsn, chunk_state) for i, s, e in pending]
            for f in as_completed(futs):
                idx, status = f.result()
                with chunk_state["lock"]:
                    chunk_state["done"] += 1
                if "FAIL" in status:
                    failures.append((idx, status))

        stop.set()
        printer.join(timeout=2)

        if failures:
            print(
                f"PARTIAL: {len(failures)} chunks failed. "
                f"Staging is preserved — re-run to resume.",
                file=sys.stderr,
            )
            for idx, msg in failures[:5]:
                print(f"   chunk {idx}: {msg}", file=sys.stderr)
            print("mf_nav_history was NOT modified.", file=sys.stderr)
            sys.exit(4)

        upload_secs = time.monotonic() - _t0
        print(f"upload done in {upload_secs:.1f}s ({chunk_state['retries']} retries)")

    # All chunks committed (this run + prior runs). Time to merge.
    print("merge: TRUNCATE + INSERT in one transaction...")
    merge = _connect(dsn)
    merge.autocommit = False
    try:
        with merge.cursor() as c:
            c.execute(f"SELECT COUNT(*) FROM {STAGING}")
            stg = c.fetchone()[0]
            print(f"   staging rows: {stg}")
            c.execute(f"SELECT COUNT(*) FROM {MANIFEST}")
            print(f"   manifest chunks: {c.fetchone()[0]}/{len(chunks)}")

            t0 = time.monotonic()
            c.execute("TRUNCATE mf_nav_history")
            print(f"   truncated old rows ({time.monotonic()-t0:.1f}s)")

            t0 = time.monotonic()
            # Source file contains duplicate (scheme_code, nav_date) rows
            # (e.g. NAV restatements). DISTINCT ON picks the highest NAV per
            # (scheme,date) so each conflict target appears once in the INSERT.
            c.execute(
                "INSERT INTO mf_nav_history "
                "  (id, scheme_code, isin, scheme_name, mf_type, nav, nav_date) "
                "SELECT gen_random_uuid(), d.scheme_code, d.isin, "
                "       d.scheme_name, d.mf_type, d.nav, d.nav_date "
                "FROM ("
                "  SELECT DISTINCT ON (s.scheme_code, to_date(s.nav_date_raw,'DD-MM-YYYY')) "
                "         s.scheme_code, "
                "         NULLIF(COALESCE(NULLIF(s.isin_growth,''), s.isin_div), '') AS isin, "
                "         m.scheme_name AS scheme_name, "
                "         COALESCE(NULLIF(m.category,''), 'Unknown') AS mf_type, "
                "         NULLIF(s.nav_raw,'')::numeric AS nav, "
                "         to_date(s.nav_date_raw, 'DD-MM-YYYY') AS nav_date "
                f"  FROM {STAGING} s "
                "  JOIN mf_fund_metadata m ON m.scheme_code = s.scheme_code "
                "  WHERE s.nav_raw IS NOT NULL AND s.nav_raw <> '' "
                "    AND s.nav_date_raw IS NOT NULL AND s.nav_date_raw <> '' "
                "  ORDER BY s.scheme_code, "
                "           to_date(s.nav_date_raw,'DD-MM-YYYY'), "
                "           NULLIF(s.nav_raw,'')::numeric DESC"
                ") d "
                "ON CONFLICT (scheme_code, nav_date) DO UPDATE SET "
                "  nav = EXCLUDED.nav, "
                "  isin = COALESCE(EXCLUDED.isin, mf_nav_history.isin), "
                "  scheme_name = EXCLUDED.scheme_name, "
                "  mf_type = EXCLUDED.mf_type"
            )
            merged = c.rowcount
            print(
                f"   inserted/updated {merged} rows in {time.monotonic()-t0:.1f}s "
                f"(skipped {stg - merged} rows w/o matching mf_fund_metadata)"
            )

            merge.commit()
            print("   committed.")

            c.execute("SELECT COUNT(*) FROM mf_nav_history")
            print(f"final mf_nav_history rows: {c.fetchone()[0]}")
    except Exception:
        merge.rollback()
        print("MERGE ROLLED BACK — staging preserved for re-merge.", file=sys.stderr)
        raise
    finally:
        merge.close()

    cleanup = _connect(dsn)
    cleanup.autocommit = True
    with cleanup.cursor() as c:
        c.execute(f"DROP TABLE {STAGING}")
        c.execute(f"DROP TABLE {MANIFEST}")
        c.execute(f"DROP TABLE {META}")
    cleanup.close()
    print("DONE.")


if __name__ == "__main__":
    main()
