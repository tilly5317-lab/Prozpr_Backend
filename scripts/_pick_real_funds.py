"""Find well-known Indian MF schemes with full 2y NAV history."""
from __future__ import annotations

import asyncio
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text
from app.database import _get_session_factory, dispose_engine

TODAY_ANCHOR = date(2026, 5, 4)
TWO_Y_AGO = TODAY_ANCHOR - timedelta(days=730)
RECENT_OK = TODAY_ANCHOR - timedelta(days=14)

# (search_pattern, category_prefix, label)
PATTERNS = [
    ("%HDFC%Flexi Cap%",                 "Equity", "HDFC Flexi Cap"),
    ("%HDFC%Mid Cap%",                   "Equity", "HDFC Mid Cap"),
    ("%HDFC%Small Cap%",                 "Equity", "HDFC Small Cap"),
    ("%HDFC%Large Cap%",                 "Equity", "HDFC Large Cap"),
    ("%HDFC%ELSS Tax%",                  "Equity", "HDFC ELSS"),
    ("%HDFC%Hybrid Equity%",             "Hybrid", "HDFC Hybrid Equity"),
    ("%HDFC%Balanced Advantage%",        "Hybrid", "HDFC BAF"),
    ("%HDFC%Corporate Bond%",            "Debt",   "HDFC Corp Bond"),
    ("%HDFC%Short Term%",                "Debt",   "HDFC Short Term"),
    ("%HDFC%Liquid%",                    "Debt",   "HDFC Liquid"),
    ("%SBI%Blue%Chip%",                  "Equity", "SBI Bluechip"),
    ("%SBI%Bluechip%",                   "Equity", "SBI Bluechip alt"),
    ("%SBI%Magnum%Mid Cap%",             "Equity", "SBI Magnum Mid Cap"),
    ("%SBI%Small Cap%",                  "Equity", "SBI Small Cap"),
    ("%SBI%Equity Hybrid%",              "Hybrid", "SBI Equity Hybrid"),
    ("%SBI%Magnum Income%",              "Debt",   "SBI Magnum Income"),
    ("%ICICI%Bluechip%",                 "Equity", "ICICI Bluechip"),
    ("%ICICI%Pru%Value%Discovery%",      "Equity", "ICICI Value Discovery"),
    ("%ICICI%Mid Cap%",                  "Equity", "ICICI Mid Cap"),
    ("%ICICI%Balanced Advantage%",       "Hybrid", "ICICI BAF"),
    ("%ICICI%Corporate Bond%",           "Debt",   "ICICI Corp Bond"),
    ("%Axis%Bluechip%",                  "Equity", "Axis Bluechip"),
    ("%Axis%Mid%Cap%",                   "Equity", "Axis Mid Cap"),
    ("%Axis%Small%Cap%",                 "Equity", "Axis Small Cap"),
    ("%Axis%ELSS%",                      "Equity", "Axis ELSS"),
    ("%Axis%Short%Term%",                "Debt",   "Axis Short Term"),
    ("%Mirae Asset%Large Cap%",          "Equity", "Mirae LC"),
    ("%Mirae Asset%Emerging%",           "Equity", "Mirae Emerging"),
    ("%Mirae Asset%ELSS%",               "Equity", "Mirae ELSS"),
    ("%Kotak%Bluechip%",                 "Equity", "Kotak Bluechip"),
    ("%Kotak%Emerging%Equity%",          "Equity", "Kotak Emerging Eq"),
    ("%Kotak%Equity Hybrid%",            "Hybrid", "Kotak Eq Hybrid"),
    ("%Kotak%Bond%",                     "Debt",   "Kotak Bond"),
    ("%Aditya Birla%Frontline%",         "Equity", "ABSL Frontline"),
    ("%Aditya Birla%Flexi Cap%",         "Equity", "ABSL Flexi"),
    ("%Aditya Birla%Mid Cap%",           "Equity", "ABSL Mid Cap"),
    ("%Aditya Birla%Corporate Bond%",    "Debt",   "ABSL Corp Bond"),
    ("%Nippon%Small Cap%",               "Equity", "Nippon Small Cap"),
    ("%Nippon%Large Cap%",               "Equity", "Nippon Large Cap"),
    ("%Nippon%Multi Cap%",               "Equity", "Nippon Multi Cap"),
    ("%Parag Parikh%Flexi Cap%",         "Equity", "PPFC"),
    ("%Parag Parikh%ELSS%",              "Equity", "PPFA ELSS"),
    ("%quant%Small Cap%",                "Equity", "Quant Small Cap"),
    ("%quant%Mid Cap%",                  "Equity", "Quant Mid Cap"),
    ("%quant%Tax%",                      "Equity", "Quant Tax"),
    ("%UTI%Nifty 50 Index%",             "Other",  "UTI Nifty 50"),
    ("%UTI%Flexi Cap%",                  "Equity", "UTI Flexi Cap"),
    ("%Tata%Digital India%",             "Equity", "Tata Digital"),
    ("%Tata%Equity P/E%",                "Equity", "Tata Equity P/E"),
    ("%DSP%Mid Cap%",                    "Equity", "DSP Mid Cap"),
    ("%DSP%Small Cap%",                  "Equity", "DSP Small Cap"),
]


async def _run() -> None:
    factory = _get_session_factory()
    async with factory() as db:
        found = []
        for pat, cat_prefix, label in PATTERNS:
            row = (await db.execute(text(
                "SELECT m.scheme_code, m.scheme_name, m.amc_name, m.category, m.sub_category, "
                "  (SELECT MIN(nav_date) FROM mf_nav_history h WHERE h.scheme_code=m.scheme_code), "
                "  (SELECT MAX(nav_date) FROM mf_nav_history h WHERE h.scheme_code=m.scheme_code) "
                "FROM mf_fund_metadata m "
                "WHERE m.is_active=true AND m.scheme_name ILIKE :pat AND m.category ILIKE :cat "
                "  AND m.plan_type='REGULAR' AND m.option_type='GROWTH' "
                "  AND m.scheme_name NOT ILIKE '%IDCW%' "
                "  AND EXISTS (SELECT 1 FROM mf_nav_history h WHERE h.scheme_code=m.scheme_code AND h.nav_date<=:two) "
                "  AND EXISTS (SELECT 1 FROM mf_nav_history h WHERE h.scheme_code=m.scheme_code AND h.nav_date>=:rec) "
                "ORDER BY (SELECT COUNT(*) FROM mf_nav_history h WHERE h.scheme_code=m.scheme_code) DESC LIMIT 1"
            ), {"pat": pat, "cat": cat_prefix + "%", "two": TWO_Y_AGO, "rec": RECENT_OK})).first()
            if row:
                found.append((label, row[0], row[1], row[3], row[4], row[5], row[6]))
                print(f"  OK {label:24} -> {row[0]:>8} | {row[3][:10]:10} | {row[4] or '-':22.22} | {row[1][:65]}")
            else:
                print(f"  -- {label:24} (no match)")
        print(f"\nFOUND {len(found)} of {len(PATTERNS)}")
        # de-dup by scheme_code
        seen = set()
        uniq = []
        for f in found:
            if f[1] in seen:
                continue
            seen.add(f[1])
            uniq.append(f)
        print(f"UNIQUE schemes: {len(uniq)}")
    await dispose_engine()


if __name__ == "__main__":
    asyncio.run(_run())
