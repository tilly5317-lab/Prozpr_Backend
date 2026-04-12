#!/usr/bin/env python3
"""
Part 1 — Mutual fund scheme list & active filter
================================================

  • Fetches latest NAV metadata for every scheme from api.mfapi.in
  • Writes ``NAV_level_data/latest.json`` (raw), plus in the project root:
    ``latest_all_mf.csv`` (all schemes + dates) and ``latest_nav_active.csv`` (recent NAV)

Run
---
  python extract_mf_funds.py
  python extract_mf_funds.py --workers 8 --active-months 3
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime

from mf_pipeline_common import (
    ACTIVE_MONTHS,
    LOG_FILE,
    MAX_WORKERS,
    OUT_ACTIVE_CSV,
    OUT_ALL_CSV,
    OUT_JSON,
    OUT_NAV_DIR,
    SCRIPT_DIR,
    step1,
    step2,
)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Part 1: fetch all MF schemes + build active list (latest NAV / dates)",
    )
    ap.add_argument(
        "--workers", type=int, default=MAX_WORKERS,
        help=f"Concurrent HTTP threads (default {MAX_WORKERS})",
    )
    ap.add_argument(
        "--active-months", type=int, default=ACTIVE_MONTHS,
        help=f"Active = NAV updated in last N months (default {ACTIVE_MONTHS})",
    )
    ap.add_argument(
        "--step", type=int, choices=[1, 2], action="append",
        help="Run only step 1 or 2 (repeatable; default: both)",
    )
    args = ap.parse_args()

    steps = set(args.step) if args.step else {1, 2}

    SCRIPT_DIR.mkdir(parents=True, exist_ok=True)
    OUT_NAV_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(LOG_FILE, encoding="utf-8"),
        ],
    )
    log = logging.getLogger("mf_pipeline")

    wall = time.monotonic()
    log.info("=" * 60)
    log.info(f"extract_mf_funds  |  steps {sorted(steps)}  |  {datetime.today():%Y-%m-%d}")
    log.info("=" * 60)

    if 1 in steps:
        step1(OUT_JSON, args.workers)

    if 2 in steps:
        if not OUT_JSON.is_file():
            log.error(f"Cannot run step 2: {OUT_JSON.name} not found. Run step 1 first.")
            return 1
        step2(OUT_JSON, OUT_ALL_CSV, OUT_ACTIVE_CSV, args.active_months)

    log.info("=" * 60)
    log.info(f"extract_mf_funds finished in {time.monotonic() - wall:.1f}s")
    log.info("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
