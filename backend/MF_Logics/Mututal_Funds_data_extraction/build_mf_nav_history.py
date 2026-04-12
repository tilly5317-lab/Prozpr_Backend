#!/usr/bin/env python3
"""
Part 2 — Per-scheme NAV history + consolidated ``mf_nav_history.txt``
======================================================================

  • Incrementally downloads historical NAV for each **active** scheme
    (from ``latest_nav_active.csv`` in the project root)
  • Merges with existing JSON under ``NAV_level_data/`` and optional seed
    under ``../MF_API/NAV_level_data/``
  • Rebuilds the tab-separated master file ``mf_nav_history.txt``

Prerequisite
------------
  Run ``python extract_mf_funds.py`` so ``latest_nav_active.csv`` exists in the project root.

Run
---
  python build_mf_nav_history.py
  python build_mf_nav_history.py --step 3          # JSON only (skip mf_nav_history rebuild)
  python build_mf_nav_history.py --step 4          # rebuild TSV from existing JSON only

Daily job (typical)
-------------------
  After you have ``latest_nav_active.csv`` from ``extract_mf_funds.py``,
  schedule only this script, e.g.::

    0 22 * * * cd /path/to/Recurring_run && python3 build_mf_nav_history.py
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime

from mf_pipeline_common import (
    HISTORY_START,
    LOG_FILE,
    MAX_WORKERS,
    OUT_ACTIVE_CSV,
    OUT_NAV_DIR,
    OUT_NAV_HISTORY,
    SCRIPT_DIR,
    SEED_NAV_DIR,
    step3,
    step4,
)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Part 2: fetch NAV history + write mf_nav_history.txt",
    )
    ap.add_argument(
        "--step", type=int, action="append", choices=[3, 4],
        help="Run only step 3 or 4 (repeatable; default: both)",
    )
    ap.add_argument(
        "--workers", type=int, default=MAX_WORKERS,
        help=f"Concurrent HTTP threads (default {MAX_WORKERS})",
    )
    ap.add_argument(
        "--history-start", default=HISTORY_START,
        help=f"Start date for new schemes YYYY-MM-DD (default {HISTORY_START})",
    )
    args = ap.parse_args()

    steps = set(args.step) if args.step else {3, 4}

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
    log.info(f"build_mf_nav_history  |  steps {sorted(steps)}  |  {datetime.today():%Y-%m-%d}")
    log.info("=" * 60)

    if 3 in steps or 4 in steps:
        if not OUT_ACTIVE_CSV.is_file():
            log.error(
                f"Missing {OUT_ACTIVE_CSV.name}. Run: python extract_mf_funds.py"
            )
            return 1

    if 3 in steps:
        step3(
            OUT_ACTIVE_CSV, OUT_NAV_DIR, SEED_NAV_DIR,
            args.history_start, args.workers,
        )

    if 4 in steps:
        step4(OUT_ACTIVE_CSV, OUT_NAV_DIR, OUT_NAV_HISTORY)

    log.info("=" * 60)
    log.info(f"build_mf_nav_history finished in {time.monotonic() - wall:.1f}s")
    log.info("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
