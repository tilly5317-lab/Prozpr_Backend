"""Reseed the 10 dummy profiles (mobiles 7770000001..7770000010) with REAL,
NAV-backed MF investment histories spanning 1 to 9 years.

Each user gets a different investment-journey length so portfolios cover the full
range (newest investor = 1y, oldest = 9y). Every BUY transaction uses the actual
``mf_nav_history`` NAV on (or just before) the trade date. Daily ``portfolio_history``
is rebuilt from those NAV time-series, so the total portfolio value fluctuates
day-to-day exactly as the real funds did.

For each profile:

1. Delete existing ``mf_transactions`` for the user.
2. Delete existing ``portfolio_holdings`` of type ``mutual_fund`` and any
   ``portfolio_history`` rows on the user's primary portfolio.
3. Pick 5-9 real Indian MF schemes (already present in ``mf_fund_metadata``)
   based on risk archetype. If a chosen fund's NAV history doesn't cover the
   user's journey start date, fall back to the next eligible fund in that role.
4. Generate a long-horizon schedule:
   - Initial lumpsum at journey start
   - Monthly SIP for the full journey
   - Annual or semi-annual top-up lumpsums
   - Stepup of SIP amount every 12 months (small)
   - Occasional recent top-ups
5. Persist ``mf_transactions`` (one BUY per scheduled event) using real NAV.
6. Aggregate holdings: ``quantity = sum(units)``, ``average_cost = invested/units``,
   ``current_price = latest NAV``, ``current_value = units * latest_nav``.
7. Backfill daily ``portfolio_history`` from journey start to today: each day's
   ``total_value = mf_units_held(d) * nav(d) + non_mf_static`` (cash/stocks/debt/other
   sourced from ``scripts/data/users.json``).
8. Refresh ``portfolios.total_value / total_invested / total_gain_percentage``
   and rebuild ``portfolio_allocations``.

Journey lengths (years):
    7770000001 = 1.0 yr  (Aarav, Aggressive, just started)
    7770000002 = 2.0 yr  (Priya)
    7770000003 = 3.5 yr  (Rohan)
    7770000004 = 5.0 yr  (Sneha)
    7770000005 = 6.0 yr  (Vikram)
    7770000006 = 7.0 yr  (Ananya)
    7770000007 = 8.0 yr  (Karthik)
    7770000008 = 8.5 yr  (Meera)
    7770000009 = 9.0 yr  (Arjun)
    7770000010 = 9.0 yr  (Divya)

NW range: 7770000001 = ~10L, 7770000010 = ~5Cr.

Usage:
    python scripts/reseed_dummy_mf_long_horizon.py            # all 10 profiles
    python scripts/reseed_dummy_mf_long_horizon.py 7770000005 # one profile only
"""

from __future__ import annotations

import asyncio
import json
import random
import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import _get_session_factory, dispose_engine

DATA_DIR = Path(__file__).resolve().parent / "data"

# Two parallel dummy series share the same 10 personas (same first_name +
# last_name); seeded by index 1..10 in both prefixes. Index 1 = newest investor
# (1 yr horizon), index 10 = longest (9 yr horizon).
MOBILE_PREFIXES = ["77700000", "90000000"]
MOBILES = [f"{p}{i:02d}" for p in MOBILE_PREFIXES for i in range(1, 11)]

TODAY = date(2026, 5, 4)  # CLAUDE.md anchor — also matches max nav_date+1
NOW = datetime.now(timezone.utc)


# Journey length per persona index (last 2 digits of mobile, 01..10).
JOURNEY_YEARS_BY_INDEX: dict[int, float] = {
    1: 1.0, 2: 2.0, 3: 3.5, 4: 5.0, 5: 6.0,
    6: 7.0, 7: 8.0, 8: 8.5, 9: 9.0, 10: 9.0,
}


def _journey_years_for(mobile: str) -> float:
    try:
        return JOURNEY_YEARS_BY_INDEX[int(mobile[-2:])]
    except (ValueError, KeyError):
        return 5.0


# ── Real fund universe (verified to have 9+ yr NAV history in DB) ──
# Each entry: (scheme_code, label, role).
FUND_UNIVERSE: list[tuple[str, str, str]] = [
    # Large cap
    ("102000", "HDFC Large Cap Fund",                        "Equity_LargeCap"),
    ("108466", "ICICI Pru Large Cap (Bluechip)",             "Equity_LargeCap"),
    ("107578", "Mirae Asset Large Cap Fund",                 "Equity_LargeCap"),
    ("106235", "Nippon India Large Cap Fund",                "Equity_LargeCap"),
    # Flexi cap / multi cap
    ("101762", "HDFC Flexi Cap Fund",                        "Equity_Flexi"),
    ("103166", "Aditya Birla SL Flexi Cap Fund",             "Equity_Flexi"),
    ("100669", "UTI Flexi Cap Fund",                         "Equity_Flexi"),
    ("101161", "Nippon India Multi Cap Fund",                "Equity_Flexi"),
    # Mid cap
    ("105758", "HDFC Mid Cap Fund",                          "Equity_MidCap"),
    ("114564", "Axis Midcap Fund",                           "Equity_MidCap"),
    ("101065", "quant Mid Cap Fund",                         "Equity_MidCap"),
    ("103819", "DSP Large & Mid Cap Fund",                   "Equity_MidCap"),
    ("100349", "ICICI Pru Large & Mid Cap Fund",             "Equity_MidCap"),
    ("100033", "Aditya Birla SL Large & Mid Cap Fund",       "Equity_MidCap"),
    # Small cap
    ("113178", "Nippon India Small Cap Fund",                "Equity_SmallCap"),
    ("100177", "quant Small Cap Fund",                       "Equity_SmallCap"),
    ("105989", "DSP Small Cap Fund",                         "Equity_SmallCap"),
    # Value
    ("102594", "ICICI Pru Value Fund",                       "Equity_Value"),
    # ELSS / Tax saver
    ("101979", "HDFC ELSS Tax Saver",                        "Equity_ELSS"),
    ("112323", "Axis ELSS Tax Saver",                        "Equity_ELSS"),
    ("100175", "quant ELSS Tax Saver",                       "Equity_ELSS"),
    # Index
    ("100822", "UTI Nifty 50 Index Fund",                    "Equity_Index"),
    # Hybrid - Aggressive
    ("102948", "HDFC Hybrid Equity Fund",                    "Hybrid_Aggressive"),
    ("102885", "SBI Equity Hybrid Fund",                     "Hybrid_Aggressive"),
    # Hybrid - BAF
    ("100119", "HDFC Balanced Advantage Fund",               "Hybrid_BAF"),
    ("104685", "ICICI Pru Balanced Advantage Fund",          "Hybrid_BAF"),
    # Debt
    ("113070", "HDFC Corporate Bond Fund",                   "Debt_CorpBond"),
    ("111987", "ICICI Pru Corporate Bond Fund",              "Debt_CorpBond"),
    ("103178", "Aditya Birla SL Corporate Bond Fund",        "Debt_CorpBond"),
    ("113047", "HDFC Short Term Debt Fund",                  "Debt_ShortDuration"),
    ("100299", "Kotak Bond Fund",                            "Debt_MedLong"),
    ("100868", "HDFC Liquid Fund",                           "Debt_Liquid"),
]
BY_ROLE: dict[str, list[tuple[str, str, str]]] = {}
for sc, nm, role in FUND_UNIVERSE:
    BY_ROLE.setdefault(role, []).append((sc, nm, role))


# ── Per-archetype slate: weight + role ──
ARCHETYPE_SLATES: dict[int, list[tuple[str, float]]] = {
    5: [
        ("Equity_LargeCap",      0.20),
        ("Equity_Flexi",         0.20),
        ("Equity_MidCap",        0.20),
        ("Equity_SmallCap",      0.18),
        ("Equity_ELSS",          0.10),
        ("Hybrid_Aggressive",    0.07),
        ("Debt_ShortDuration",   0.05),
    ],
    4: [
        ("Equity_LargeCap",      0.22),
        ("Equity_Flexi",         0.20),
        ("Equity_MidCap",        0.18),
        ("Equity_SmallCap",      0.12),
        ("Equity_ELSS",          0.08),
        ("Hybrid_Aggressive",    0.10),
        ("Debt_CorpBond",        0.10),
    ],
    3: [
        ("Equity_LargeCap",      0.25),
        ("Equity_Flexi",         0.20),
        ("Equity_MidCap",        0.13),
        ("Equity_Index",         0.08),
        ("Hybrid_BAF",           0.12),
        ("Hybrid_Aggressive",    0.08),
        ("Debt_CorpBond",        0.10),
        ("Debt_ShortDuration",   0.04),
    ],
    2: [
        ("Equity_LargeCap",      0.25),
        ("Equity_Flexi",         0.15),
        ("Equity_Index",         0.10),
        ("Hybrid_BAF",           0.18),
        ("Debt_CorpBond",        0.16),
        ("Debt_ShortDuration",   0.10),
        ("Debt_Liquid",          0.06),
    ],
    1: [
        ("Equity_LargeCap",      0.18),
        ("Equity_Index",         0.10),
        ("Hybrid_BAF",           0.20),
        ("Debt_CorpBond",        0.20),
        ("Debt_ShortDuration",   0.14),
        ("Debt_MedLong",         0.10),
        ("Debt_Liquid",          0.08),
    ],
}


def _load_users_json() -> dict[str, dict]:
    """Map mobile -> profile.

    The 7770000xxx and 9000000xxx series share the same 10 personas; we key the
    base profiles by ``users.json`` mobile and then synthesize matching profiles
    for the second prefix so ``_seed_one_user`` can find them by mobile.
    """
    with open(DATA_DIR / "users.json", encoding="utf-8") as f:
        rows = json.load(f)
    by_mobile = {r["mobile"]: r for r in rows}
    # Mirror each 7770000xx profile under its 9000000xx twin (same persona).
    twins: dict[str, dict] = {}
    for mobile, prof in by_mobile.items():
        if mobile.startswith("77700000"):
            twin_mobile = "90000000" + mobile[-2:]
            twins[twin_mobile] = {**prof, "mobile": twin_mobile}
    by_mobile.update(twins)
    return by_mobile


async def _earliest_nav(db: AsyncSession, scheme_code: str) -> Optional[date]:
    return (await db.execute(text(
        "SELECT MIN(nav_date) FROM mf_nav_history WHERE scheme_code=:sc"
    ), {"sc": scheme_code})).scalar()


async def _pick_funds_for_user(
    db: AsyncSession, mobile: str, risk_level: int, journey_start: date
) -> list[tuple[str, str, float]]:
    """Returns list of (scheme_code, scheme_label, weight) summing to 1.0.

    Rotates through candidates per role to prefer ones whose NAV history covers
    the user's journey_start. Falls back to any candidate in that role if none
    do.
    """
    rng = random.Random(int(sha256(mobile.encode()).hexdigest(), 16) % (2**32))
    slate = ARCHETYPE_SLATES.get(risk_level, ARCHETYPE_SLATES[3])
    picked: list[tuple[str, str, float]] = []
    for role, weight in slate:
        candidates = list(BY_ROLE.get(role, []))
        if not candidates:
            continue
        rng.shuffle(candidates)
        chosen = None
        for sc, nm, _ in candidates:
            earliest = await _earliest_nav(db, sc)
            if earliest is not None and earliest <= journey_start:
                chosen = (sc, nm)
                break
        if chosen is None:
            sc, nm, _ = candidates[0]
            chosen = (sc, nm)
        picked.append((chosen[0], chosen[1], weight))
    s = sum(w for _, _, w in picked) or 1.0
    return [(sc, nm, w / s) for sc, nm, w in picked]


async def _nav_series(db: AsyncSession, scheme_code: str, start: date, end: date) -> dict[date, Decimal]:
    rows = (await db.execute(text(
        "SELECT nav_date, nav FROM mf_nav_history WHERE scheme_code=:sc "
        "AND nav_date BETWEEN :s AND :e ORDER BY nav_date"
    ), {"sc": scheme_code, "s": start, "e": end})).all()
    return {r[0]: r[1] for r in rows}


async def _build_daily_nav_lookup(
    db: AsyncSession, scheme_code: str, start: date, end: date
) -> dict[date, Decimal]:
    """Map every calendar day in [start, end] to NAV-on-or-before-that-day."""
    series = await _nav_series(db, scheme_code, start - timedelta(days=30), end)
    if not series:
        return {}
    sorted_dates = sorted(series.keys())
    out: dict[date, Decimal] = {}
    last: Optional[Decimal] = None
    j = 0
    cur = start
    while cur <= end:
        while j < len(sorted_dates) and sorted_dates[j] <= cur:
            last = series[sorted_dates[j]]
            j += 1
        if last is not None:
            out[cur] = last
        cur += timedelta(days=1)
    if not out and series:
        first_nav = series[sorted_dates[0]]
        cur = start
        while cur <= end:
            out[cur] = first_nav
            cur += timedelta(days=1)
    return out


def _generate_long_schedule(
    rng: random.Random, mobile: str, scheme_code: str, journey_start: date
) -> list[tuple[date, str, float]]:
    """Long-horizon transaction plan.

    Returns chronological [(date, kind, weight)]. ``weight`` is a relative-amount
    unit; the final rupee scale is fitted later so total current value matches
    the per-fund target.

    Pattern (mirrors a long-term Indian retail investor):
    1. Initial lumpsum near journey start (large weight).
    2. Monthly SIP from month +1 onward, with annual SIP step-up (~10%/yr).
    3. Annual or semi-annual top-up lumpsums (smaller).
    4. Recent top-up in last 6-18 months.
    """
    debit_day = (int(sha256((mobile + scheme_code).encode()).hexdigest()[:4], 16) % 25) + 3
    out: list[tuple[date, str, float]] = []

    # 1) Initial lumpsum a few days after journey_start
    initial_d = journey_start + timedelta(days=rng.randint(2, 14))
    out.append((initial_d, "LUMP", 36.0))

    # 2) Monthly SIPs from month +1 onward, ramping with annual step-up
    months_total = max(1, int((TODAY - journey_start).days / 30))
    cur_m = journey_start + timedelta(days=30)
    sip_weight = 1.0
    months_in_year = 0
    for _ in range(months_total):
        if cur_m > TODAY:
            break
        try:
            d = date(cur_m.year, cur_m.month, debit_day)
        except ValueError:
            d = date(cur_m.year, cur_m.month, min(debit_day, 28))
        if initial_d < d <= TODAY:
            out.append((d, "SIP", sip_weight))
        cur_m = cur_m + timedelta(days=30)
        months_in_year += 1
        if months_in_year >= 12:
            months_in_year = 0
            sip_weight *= 1.10  # annual SIP step-up

    # 3) Annual / semi-annual top-up lumpsums
    journey_days = (TODAY - journey_start).days
    n_topups = max(1, journey_days // 240)  # roughly every 8 months
    for i in range(n_topups):
        offset = int(journey_days * (i + 1) / (n_topups + 1))
        d = journey_start + timedelta(days=offset + rng.randint(-10, 10))
        if initial_d < d <= TODAY:
            out.append((d, "LUMP", rng.uniform(6.0, 14.0)))

    # 4) Recent top-up in last 6-18 months (high probability)
    if rng.random() < 0.85 and journey_days > 200:
        d = TODAY - timedelta(days=rng.randint(45, 540))
        if d > initial_d:
            out.append((d, "LUMP", rng.uniform(5.0, 12.0)))

    return sorted(out, key=lambda t: t[0])


def _txn_fingerprint(user_id: str, scheme_code: str, folio: str, kind: str,
                     d: date, units: Decimal, nav: Decimal, amount: Decimal, idx: int) -> str:
    raw = f"{user_id}|{scheme_code}|{folio}|{kind}|{d}|{units}|{nav}|{amount}|{idx}|reseed_long_horizon_v1"
    return sha256(raw.encode()).hexdigest()


async def _seed_one_user(db: AsyncSession, profile: dict) -> dict:
    mobile = profile["mobile"]
    nw = float(profile["net_worth"])
    risk_level = int(profile.get("risk_level", 3))
    mf_pct = float(profile.get("mf_pct", 35))
    cash_pct = float(profile.get("cash_pct", 10))
    stock_pct = float(profile.get("stock_pct", 20))
    debt_pct = float(profile.get("debt_pct", 20))
    other_pct = float(profile.get("other_pct", 10))
    target_mf_value = nw * mf_pct / 100.0

    journey_yrs = _journey_years_for(mobile)
    journey_start = TODAY - timedelta(days=int(journey_yrs * 365))

    user_id = (await db.execute(text(
        "SELECT id FROM users WHERE mobile=:m"), {"m": mobile})).scalar()
    if user_id is None:
        raise RuntimeError(f"user {mobile} not found")

    portfolio_id = (await db.execute(text(
        "SELECT id FROM portfolios WHERE user_id=:u AND is_primary=true LIMIT 1"
    ), {"u": user_id})).scalar()
    if portfolio_id is None:
        portfolio_id = (await db.execute(text(
            "INSERT INTO portfolios (id, user_id, name, total_value, total_invested, "
            "total_gain_percentage, is_primary, created_at, updated_at) "
            "VALUES (gen_random_uuid(), :u, 'Primary', 0, 0, 0, true, now(), now()) RETURNING id"
        ), {"u": user_id})).scalar()

    # 1. ── Erase prior MF state ──
    await db.execute(text("DELETE FROM mf_transactions WHERE user_id=:u"), {"u": user_id})
    await db.execute(text(
        "DELETE FROM portfolio_holdings WHERE portfolio_id=:p AND instrument_type='mutual_fund'"
    ), {"p": portfolio_id})
    await db.execute(text(
        "DELETE FROM portfolio_history WHERE portfolio_id=:p"), {"p": portfolio_id})

    # 2. ── Plan funds + amounts ──
    rng = random.Random(int(sha256(mobile.encode()).hexdigest(), 16) % (2**32))
    fund_plan = await _pick_funds_for_user(db, mobile, risk_level, journey_start)

    fund_targets: list[tuple[str, str, float]] = [
        (sc, nm, target_mf_value * w) for sc, nm, w in fund_plan
    ]

    folio = f"FOLIO-{mobile[-4:]}"
    txns_per_fund: dict[str, list[dict]] = {}
    nav_lookup_per_fund: dict[str, dict[date, Decimal]] = {}

    for sc, nm, target_cv in fund_targets:
        nav_map = await _build_daily_nav_lookup(db, sc, journey_start, TODAY)
        if not nav_map:
            continue
        nav_lookup_per_fund[sc] = nav_map
        latest_nav = float(nav_map[max(nav_map.keys())])

        sched = _generate_long_schedule(rng, mobile, sc, journey_start)
        if not sched:
            txns_per_fund[sc] = []
            continue

        # Fit scale so realised current value == target_cv exactly.
        cv_per_rupee = 0.0
        sum_weights = 0.0
        for d, kind, w in sched:
            nav_then = nav_map.get(d)
            if nav_then is None:
                continue
            cv_per_rupee += w * (latest_nav / float(nav_then))
            sum_weights += w
        if cv_per_rupee <= 0 or sum_weights <= 0:
            txns_per_fund[sc] = []
            continue
        scale = target_cv / cv_per_rupee  # rupees per unit-weight

        rows = []
        for d, kind, w in sched:
            nav_then = nav_map.get(d)
            if nav_then is None:
                continue
            amount = round(scale * w, 2)
            if amount < 100:
                continue
            units = round(amount / float(nav_then), 4)
            rows.append({
                "date": d, "kind": kind, "amount": amount,
                "nav": float(nav_then), "units": units,
            })
        txns_per_fund[sc] = rows

    # 3. ── Persist mf_transactions ──
    total_invested = 0.0
    txn_count = 0
    for sc, rows in txns_per_fund.items():
        for idx, r in enumerate(rows):
            fp = _txn_fingerprint(str(user_id), sc, folio, "BUY",
                                   r["date"], Decimal(str(r["units"])),
                                   Decimal(str(r["nav"])), Decimal(str(r["amount"])), idx)
            stamp_duty = round(r["amount"] * 0.00005, 2) if r["amount"] >= 1000 else 0.0
            await db.execute(text(
                "INSERT INTO mf_transactions (id, user_id, scheme_code, folio_number, "
                "transaction_type, transaction_date, units, nav, amount, stamp_duty, "
                "source_system, source_txn_fingerprint, created_at) "
                "VALUES (gen_random_uuid(), :u, :sc, :f, 'BUY', :d, :un, :nv, :am, :sd, "
                "'BACKFILL', :fp, now())"
            ), {
                "u": user_id, "sc": sc, "f": folio, "d": r["date"],
                "un": r["units"], "nv": r["nav"], "am": r["amount"],
                "sd": stamp_duty, "fp": fp,
            })
            total_invested += r["amount"]
            txn_count += 1

    # 4. ── Compute & persist holdings ──
    fund_metas: dict[str, dict] = {}
    for sc in txns_per_fund.keys():
        row = (await db.execute(text(
            "SELECT scheme_name, category, sub_category, amc_name, regular_plan_fees, "
            "  returns_1y_pct, returns_3y_pct, returns_5y_pct "
            "FROM mf_fund_metadata WHERE scheme_code=:sc"
        ), {"sc": sc})).first()
        if row is None:
            fund_metas[sc] = {}
        else:
            fund_metas[sc] = {
                "scheme_name": row[0], "category": row[1], "sub_category": row[2],
                "amc_name": row[3], "regular_plan_fees": row[4],
                "returns_1y_pct": row[5], "returns_3y_pct": row[6], "returns_5y_pct": row[7],
            }

    holdings_summary: list[dict] = []
    mf_current_value = 0.0
    mf_invested = 0.0

    def _clip(val, lo: float, hi: float):
        if val is None:
            return None
        try:
            v = float(val)
        except (TypeError, ValueError):
            return None
        if v != v:  # NaN
            return None
        return max(lo, min(hi, v))

    for sc, rows in txns_per_fund.items():
        if not rows:
            continue
        nav_map = nav_lookup_per_fund.get(sc, {})
        if not nav_map:
            continue
        latest_nav = float(nav_map[max(nav_map.keys())])
        units = sum(r["units"] for r in rows)
        invested = sum(r["amount"] for r in rows)
        avg_cost = invested / units if units else 0.0
        cv = round(units * latest_nav, 2)
        meta = fund_metas.get(sc, {})
        scheme_name = meta.get("scheme_name") or sc

        er_raw = meta.get("regular_plan_fees")
        expense_ratio = _clip(er_raw, 0.0, 9.99)
        r1 = _clip(meta.get("returns_1y_pct"), -99999.99, 99999.99)
        r3 = _clip(meta.get("returns_3y_pct"), -99999.99, 99999.99)
        r5 = _clip(meta.get("returns_5y_pct"), -99999.99, 99999.99)
        await db.execute(text(
            "INSERT INTO portfolio_holdings (id, portfolio_id, instrument_name, instrument_type, "
            "ticker_symbol, quantity, average_cost, current_price, current_value, "
            "expense_ratio, return_1y, return_3y, return_5y, created_at, updated_at) "
            "VALUES (gen_random_uuid(), :p, :nm, 'mutual_fund', :tk, :qty, :ac, :cp, :cv, "
            ":er, :r1, :r3, :r5, now(), now())"
        ), {
            "p": portfolio_id,
            "nm": scheme_name[:255], "tk": sc[:20],
            "qty": round(units, 4), "ac": round(avg_cost, 4),
            "cp": round(latest_nav, 4), "cv": cv,
            "er": expense_ratio, "r1": r1, "r3": r3, "r5": r5,
        })
        holdings_summary.append({"scheme_code": sc, "name": scheme_name,
                                  "units": units, "invested": invested, "current_value": cv,
                                  "latest_nav": latest_nav})
        mf_current_value += cv
        mf_invested += invested

    # 5. ── Daily portfolio history (NAV-driven for MF, static for non-MF) ──
    cash_amt = round(nw * cash_pct / 100, 2)
    stock_amt = round(nw * stock_pct / 100, 2)
    debt_amt = round(nw * debt_pct / 100, 2)
    other_amt = round(nw * other_pct / 100, 2)

    fund_unit_schedule: dict[str, list[tuple[date, float]]] = {}
    for sc, rows in txns_per_fund.items():
        cum = 0.0
        events = []
        for r in sorted(rows, key=lambda x: x["date"]):
            cum += r["units"]
            events.append((r["date"], cum))
        fund_unit_schedule[sc] = events

    def _units_held(sc: str, d: date) -> float:
        events = fund_unit_schedule.get(sc, [])
        held = 0.0
        for ev_date, cum in events:
            if ev_date <= d:
                held = cum
            else:
                break
        return held

    cur = journey_start
    history_rows: list[tuple[date, float]] = []
    while cur <= TODAY:
        mf_val = 0.0
        for sc, _, _ in fund_targets:
            nav_map = nav_lookup_per_fund.get(sc, {})
            nav_then = nav_map.get(cur)
            if nav_then is None:
                continue
            held = _units_held(sc, cur)
            if held <= 0:
                continue
            mf_val += held * float(nav_then)
        # Tiny daily noise on equity/debt buckets so the static portion still
        # fluctuates a little (deterministic, ±1% range).
        day_noise = (((hash((mobile, cur.toordinal())) % 200) - 100) / 10000.0)
        equity_proxy = stock_amt * (1 + day_noise * 0.5)
        debt_proxy = debt_amt * (1 + day_noise * 0.05)
        cash_proxy = cash_amt
        other_proxy = other_amt
        total = mf_val + equity_proxy + debt_proxy + cash_proxy + other_proxy
        history_rows.append((cur, round(total, 2)))
        cur += timedelta(days=1)

    # Bulk insert history (chunks of 200)
    for i in range(0, len(history_rows), 200):
        batch = history_rows[i:i + 200]
        if not batch:
            continue
        values = ", ".join(
            f"(gen_random_uuid(), :p, :d{k}, :v{k}, now())" for k in range(len(batch))
        )
        params: dict[str, object] = {"p": portfolio_id}
        for k, (d, v) in enumerate(batch):
            params[f"d{k}"] = d
            params[f"v{k}"] = v
        await db.execute(text(
            "INSERT INTO portfolio_history (id, portfolio_id, recorded_date, total_value, "
            f"created_at) VALUES {values}"
        ), params)

    # 6. ── Refresh portfolio totals + allocations ──
    non_mf_amt = cash_amt + stock_amt + debt_amt + other_amt
    final_total_value = round(mf_current_value + non_mf_amt, 2)
    final_total_invested = round(mf_invested + non_mf_amt, 2)
    gain_pct = round((final_total_value - final_total_invested) / final_total_invested * 100, 2) \
        if final_total_invested > 0 else None

    await db.execute(text(
        "UPDATE portfolios SET total_value=:tv, total_invested=:ti, total_gain_percentage=:gp, "
        "  updated_at=now() WHERE id=:p"
    ), {"tv": final_total_value, "ti": final_total_invested, "gp": gain_pct, "p": portfolio_id})

    await db.execute(text("DELETE FROM portfolio_allocations WHERE portfolio_id=:p"),
                     {"p": portfolio_id})
    equity_total = round(mf_current_value + stock_amt, 2)
    for asset_class, amt in [
        ("Cash", cash_amt), ("Equity", equity_total),
        ("Debt", debt_amt), ("Other", other_amt),
    ]:
        if amt <= 0:
            continue
        await db.execute(text(
            "INSERT INTO portfolio_allocations (id, portfolio_id, asset_class, "
            "allocation_percentage, amount, created_at, updated_at) "
            "VALUES (gen_random_uuid(), :p, :ac, :pct, :am, now(), now())"
        ), {
            "p": portfolio_id, "ac": asset_class,
            "pct": round(amt / final_total_value * 100, 2) if final_total_value > 0 else 0,
            "am": amt,
        })

    return {
        "mobile": mobile,
        "name": f"{profile.get('first_name', '')} {profile.get('last_name', '')}".strip(),
        "journey_yrs": journey_yrs,
        "journey_start": journey_start.isoformat(),
        "nw_target": nw,
        "mf_target": target_mf_value,
        "mf_invested": round(mf_invested, 2),
        "mf_current_value": round(mf_current_value, 2),
        "mf_pnl": round(mf_current_value - mf_invested, 2),
        "mf_pnl_pct": round((mf_current_value - mf_invested) / mf_invested * 100, 2) if mf_invested else 0,
        "txns": txn_count,
        "funds": len(holdings_summary),
        "history_days": len(history_rows),
        "final_nw": final_total_value,
    }


async def reseed_one(mobile: str) -> dict:
    profiles = _load_users_json()
    if mobile not in profiles:
        raise RuntimeError(f"mobile {mobile} not in users.json")
    factory = _get_session_factory()
    async with factory() as db:
        try:
            info = await _seed_one_user(db, profiles[mobile])
            await db.commit()
            return info
        except Exception:
            await db.rollback()
            raise


async def reseed_all() -> list[dict]:
    profiles = _load_users_json()
    results = []
    for mobile in MOBILES:
        if mobile not in profiles:
            print(f"  SKIP {mobile} (not in users.json)")
            continue
        print(f"  Seeding {mobile} ({profiles[mobile]['first_name']} "
              f"{profiles[mobile].get('last_name', '')}, journey={_journey_years_for(mobile)}y)... ",
              end="", flush=True)
        factory = _get_session_factory()
        async with factory() as db:
            try:
                info = await _seed_one_user(db, profiles[mobile])
                await db.commit()
                print(f"OK  txns={info['txns']:>4}  funds={info['funds']}  "
                      f"invested={info['mf_invested']:>14,.0f}  "
                      f"current={info['mf_current_value']:>14,.0f}  "
                      f"pnl%={info['mf_pnl_pct']:>+7.2f}  "
                      f"days={info['history_days']}")
                results.append(info)
            except Exception as exc:
                await db.rollback()
                import traceback
                traceback.print_exc()
                print(f"FAIL: {exc}")
        await dispose_engine()
    return results


def _print_table(results: list[dict]) -> None:
    print("\n" + "=" * 130)
    print(f"{'Mobile':<12} {'Name':<22} {'Journey':>8} {'Start':>12} "
          f"{'MF Invested':>14} {'MF Current':>14} {'PnL':>14} {'PnL%':>8} "
          f"{'Funds':>5} {'Txns':>5} {'Hist':>5}")
    print("-" * 130)
    for r in results:
        print(f"{r['mobile']:<12} "
              f"{r['name'][:21]:<22} "
              f"{r['journey_yrs']:>7.1f}y "
              f"{r['journey_start']:>12} "
              f"{r['mf_invested']:>14,.0f} "
              f"{r['mf_current_value']:>14,.0f} "
              f"{r['mf_pnl']:>+14,.0f} "
              f"{r['mf_pnl_pct']:>+7.2f}% "
              f"{r['funds']:>5} "
              f"{r['txns']:>5} "
              f"{r['history_days']:>5}")
    print("=" * 130)
    print("\nMobile numbers (serial):")
    for i, r in enumerate(results, 1):
        print(f"  {i:>2}. {r['mobile']}   ({r['name']}, {r['journey_yrs']:.1f}y horizon)")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        mob = sys.argv[1]
        info = asyncio.run(reseed_one(mob))
        _print_table([info])
    else:
        results = asyncio.run(reseed_all())
        _print_table(results)
