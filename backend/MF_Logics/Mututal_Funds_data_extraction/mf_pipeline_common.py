#!/usr/bin/env python3
"""
Shared helpers and pipeline steps for MF API fetch → NAV consolidation.
"""
from __future__ import annotations

import csv
import http.client
import json
import logging
import ssl
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

# ── Paths (defaults; callers may override via their own constants) ───────
SCRIPT_DIR = Path(__file__).resolve().parent
SEED_NAV_DIR = SCRIPT_DIR.parent / "MF_API" / "NAV_level_data"

OUT_NAV_DIR    = SCRIPT_DIR / "NAV_level_data"
OUT_JSON       = OUT_NAV_DIR / "latest.json"
OUT_ALL_CSV    = SCRIPT_DIR / "latest_all_mf.csv"
OUT_ACTIVE_CSV = SCRIPT_DIR / "latest_nav_active.csv"
OUT_NAV_HISTORY = SCRIPT_DIR / "mf_nav_history.txt"
LOG_FILE       = OUT_NAV_DIR / "pipeline.log"

# ── Defaults ──────────────────────────────────────────────────────────────
BASE_URL      = "https://api.mfapi.in"
MAX_WORKERS   = 12
ACTIVE_MONTHS = 3
HISTORY_START = "2023-01-01"

# ── HTTP / logging ───────────────────────────────────────────────────────
_ctx = ssl.create_default_context()
log  = logging.getLogger("mf_pipeline")


def _read_body(resp) -> bytes:
    """Read full response body, tolerating Content-Length mismatches."""
    try:
        return resp.read()
    except http.client.IncompleteRead as e:
        return e.partial


def _get(url: str, timeout: float = 120.0, retries: int = 3) -> Any:
    """Fetch JSON from *url*, retrying on truncated responses."""
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "Accept": "application/json",
                    "Accept-Encoding": "identity",
                    "User-Agent": "MF-daily/1.0",
                },
            )
            with urllib.request.urlopen(req, context=_ctx, timeout=timeout) as r:
                raw = _read_body(r)
                return json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, http.client.IncompleteRead) as e:
            last_exc = e
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
    raise last_exc  # type: ignore[misc]


def _get_array(url: str, timeout: float = 120.0) -> list:
    """Fetch a JSON **array**, recovering from truncated responses."""
    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "Accept": "application/json",
                    "Accept-Encoding": "identity",
                    "User-Agent": "MF-daily/1.0",
                },
            )
            with urllib.request.urlopen(req, context=_ctx, timeout=timeout) as r:
                raw = _read_body(r).decode("utf-8")

            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                pos = raw.rfind("},")
                if pos > 0:
                    repaired = raw[: pos + 1] + "]"
                    return json.loads(repaired)
                raise
        except (json.JSONDecodeError, http.client.IncompleteRead) as e:
            last_exc = e
            time.sleep(2 ** attempt)
    raise last_exc  # type: ignore[misc]


def _parse_dd(s: str) -> datetime | None:
    """Parse DD-MM-YYYY (mfapi.in native format)."""
    try:
        return datetime.strptime(s, "%d-%m-%Y")
    except (ValueError, TypeError):
        return None


def _dd_to_mm(s: str) -> str:
    """DD-MM-YYYY  →  MM-DD-YYYY."""
    dt = _parse_dd(s)
    return dt.strftime("%m-%d-%Y") if dt else s


# ═════════════════════════════════════════════════════════════════════════
#  STEP 1 – Fetch latest NAV for every scheme
# ═════════════════════════════════════════════════════════════════════════
def step1(out: Path, workers: int) -> int:
    log.info("── Step 1 · Fetch latest NAV for all schemes ──")

    PAGE = 200
    codes: list[int] = []
    off = 0
    while True:
        batch = _get_array(f"{BASE_URL}/mf?limit={PAGE}&offset={off}")
        if not batch:
            break
        codes.extend(int(r["schemeCode"]) for r in batch)
        if len(batch) < PAGE:
            break
        off += len(batch)
    log.info(f"  Discovered {len(codes)} scheme codes")

    def _fetch(c: int) -> dict | None:
        try:
            p = _get(f"{BASE_URL}/mf/{c}/latest", 45)
        except Exception:
            return None
        if not isinstance(p, dict) or p.get("status") != "SUCCESS":
            return None
        m   = p.get("meta") or {}
        pts = p.get("data") or []
        if not pts:
            return None
        pt = pts[0]
        return {
            "schemeCode":          m.get("scheme_code", c),
            "schemeName":          m.get("scheme_name"),
            "fundHouse":           m.get("fund_house"),
            "schemeType":          m.get("scheme_type"),
            "schemeCategory":      m.get("scheme_category"),
            "isinGrowth":          m.get("isin_growth"),
            "isinDivReinvestment": m.get("isin_div_reinvestment"),
            "nav":                 pt.get("nav"),
            "date":                pt.get("date"),
        }

    rows: list[dict] = []
    fails = 0
    t0 = time.monotonic()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        fmap = {ex.submit(_fetch, c): c for c in codes}
        for i, fut in enumerate(as_completed(fmap), 1):
            if i % 2000 == 0 or i == len(codes):
                log.info(f"    {i}/{len(codes)} ({time.monotonic() - t0:.0f}s)")
            r = fut.result()
            if r is not None:
                rows.append(r)
            else:
                fails += 1

    rows.sort(key=lambda r: int(r["schemeCode"]))
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False)

    elapsed = time.monotonic() - t0
    log.info(f"  Done: {len(rows)} schemes, {fails} failures ({elapsed:.1f}s)")
    return len(rows)


# ═════════════════════════════════════════════════════════════════════════
#  STEP 2 – Filter active funds & extract sub-category
# ═════════════════════════════════════════════════════════════════════════
def step2(src_json: Path, out_all: Path, out_active: Path, months: int) -> int:
    log.info("── Step 2 · Filter active funds & extract sub-category ──")

    with open(src_json, encoding="utf-8") as f:
        rows = json.load(f)

    cutoff = datetime.today() - timedelta(days=months * 30)

    def _sub(cat: str) -> str:
        return cat.split(" - ", 1)[1] if cat and " - " in cat else (cat or "")

    all_hdr = [
        "schemeCode", "schemeName", "fundHouse", "schemeType",
        "schemeCategory", "isinGrowth", "isinDivReinvestment",
        "nav", "date", "date_mm_dd_yyyy",
    ]
    out_all.parent.mkdir(parents=True, exist_ok=True)
    with open(out_all, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=all_hdr)
        w.writeheader()
        for r in rows:
            row = {k: r.get(k, "") or "" for k in all_hdr}
            row["date_mm_dd_yyyy"] = _dd_to_mm(r.get("date", ""))
            w.writerow(row)

    act_hdr = [
        "schemeCode", "schemeName", "fundHouse", "schemeType",
        "schemeCategory", "sub_category", "isinGrowth",
        "isinDivReinvestment", "nav", "date",
    ]
    active: list[dict] = []
    for r in rows:
        dt = _parse_dd(r.get("date", ""))
        if dt and dt >= cutoff:
            row = {k: r.get(k, "") or "" for k in act_hdr}
            row["sub_category"] = _sub(r.get("schemeCategory", ""))
            row["date"] = _dd_to_mm(r.get("date", ""))
            active.append(row)

    with open(out_active, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=act_hdr)
        w.writeheader()
        for r in active:
            w.writerow(r)

    log.info(f"  All schemes : {len(rows)} -> {out_all.name}")
    log.info(f"  Active (last {months} mo): {len(active)} -> {out_active.name}")
    return len(active)


# ═════════════════════════════════════════════════════════════════════════
#  STEP 3 – Incremental historical NAV
# ═════════════════════════════════════════════════════════════════════════
def step3(
    active_csv: Path,
    out_dir: Path,
    seed_dir: Path,
    history_start: str,
    workers: int,
) -> dict[str, int]:
    log.info("── Step 3 · Incremental historical NAV fetch ──")

    codes: list[int] = []
    seen: set[int] = set()
    with open(active_csv, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            c = int(row["schemeCode"])
            if c not in seen:
                seen.add(c)
                codes.append(c)

    out_dir.mkdir(parents=True, exist_ok=True)
    today_str = datetime.today().strftime("%Y-%m-%d")

    def _load_existing(code: int) -> tuple[list[dict], dict]:
        for d in (out_dir, seed_dir):
            p = d / f"{code}.json"
            if not p.is_file():
                continue
            try:
                obj = json.loads(p.read_text("utf-8"))
                if isinstance(obj, dict) and obj.get("status") == "SUCCESS":
                    return obj.get("data") or [], obj.get("meta") or {}
            except Exception:
                pass
        return [], {}

    def _process(code: int) -> tuple[int, str]:
        old_data, old_meta = _load_existing(code)
        start = history_start

        if old_data:
            newest = _parse_dd(old_data[0].get("date", ""))
            if newest:
                nxt = newest + timedelta(days=1)
                if nxt.date() > datetime.today().date():
                    dst = out_dir / f"{code}.json"
                    if not dst.is_file():
                        dst.write_text(
                            json.dumps(
                                {"meta": old_meta, "status": "SUCCESS", "data": old_data},
                                ensure_ascii=False, indent=2,
                            ),
                            "utf-8",
                        )
                    return code, "up_to_date"
                start = nxt.strftime("%Y-%m-%d")

        url = f"{BASE_URL}/mf/{code}?startDate={start}&endDate={today_str}"
        try:
            payload = _get(url)
        except Exception:
            return code, "http_error"

        if not isinstance(payload, dict) or payload.get("status") != "SUCCESS":
            return code, "up_to_date" if old_data else "not_success"

        new_data = payload.get("data") or []
        new_meta = payload.get("meta") or {}

        if not new_data and not old_data:
            return code, "no_data"

        if old_data:
            new_dates = {p["date"] for p in new_data}
            merged = list(new_data) + [
                p for p in old_data if p.get("date") not in new_dates
            ]
            merged.sort(
                key=lambda p: _parse_dd(p.get("date", "")) or datetime.min,
                reverse=True,
            )
        else:
            merged = new_data

        final = {
            "meta": new_meta or old_meta,
            "status": "SUCCESS",
            "data": merged,
        }
        try:
            (out_dir / f"{code}.json").write_text(
                json.dumps(final, ensure_ascii=False, indent=2), "utf-8",
            )
        except OSError:
            return code, "write_error"

        return code, "updated" if old_data else "new"

    stats: dict[str, int] = {}
    t0 = time.monotonic()
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        fmap = {ex.submit(_process, c): c for c in codes}
        for i, fut in enumerate(as_completed(fmap), 1):
            if i % 500 == 0 or i == len(codes):
                log.info(f"    {i}/{len(codes)} ({time.monotonic() - t0:.0f}s)")
            _, st = fut.result()
            stats[st] = stats.get(st, 0) + 1

    elapsed = time.monotonic() - t0
    log.info(f"  Done in {elapsed:.1f}s: {stats}")
    return stats


# ═════════════════════════════════════════════════════════════════════════
#  STEP 4 – Build consolidated NAV history table (TSV)
# ═════════════════════════════════════════════════════════════════════════
def step4(active_csv: Path, nav_dir: Path, out: Path) -> int:
    log.info("── Step 4 · Build mf_nav_history (master NAV table) ──")

    sub_map: dict[int, str] = {}
    with open(active_csv, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            sub_map[int(row["schemeCode"])] = (row.get("sub_category") or "").strip()

    jsons = sorted(
        (p for p in nav_dir.glob("*.json") if p.stem.isdigit()),
        key=lambda p: int(p.stem),
    )
    header = (
        "fund_house", "scheme_code", "scheme_category",
        "Subcategory", "scheme_name", "date", "nav",
    )

    n = 0
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t", lineterminator="\n")
        w.writerow(header)

        for i, p in enumerate(jsons, 1):
            if i % 500 == 0 or i == len(jsons):
                log.info(f"    {i}/{len(jsons)} files, {n:,} rows")
            try:
                d = json.loads(p.read_text("utf-8"))
            except Exception:
                continue
            if not isinstance(d, dict) or d.get("status") != "SUCCESS":
                continue

            m = d.get("meta") or {}
            try:
                sc = int(m.get("scheme_code", p.stem))
            except (TypeError, ValueError):
                continue

            fh  = m.get("fund_house", "")
            cat = m.get("scheme_category", "")
            nm  = m.get("scheme_name", "")
            sub = sub_map.get(sc, "")

            for pt in d.get("data") or []:
                if isinstance(pt, dict):
                    w.writerow([
                        fh, str(sc), cat, sub, nm,
                        pt.get("date", ""), pt.get("nav", ""),
                    ])
                    n += 1

    log.info(f"  Done: {n:,} rows -> {out.name}")
    return n
