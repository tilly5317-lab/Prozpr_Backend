"""Wipe ALL users from the DB and recreate a clean set of 10 dummy profiles
with the new ``5550000001..5550000010`` mobile series, then seed long-horizon
NAV-backed MF history for each.

The wipe relies on FK ``ON DELETE CASCADE`` to clean every user-derived row
(portfolios, holdings, transactions, history, profile tables, chat, goals,
notifications, ...). It deliberately preserves reference data:

- ``mf_fund_metadata`` (fund universe)
- ``mf_nav_history`` (10 yr NAV time series)
- ``company_metadata`` and ``stock_price_history``
- ``alembic_version``

After the wipe it:

1. Creates 10 new users (mobile ``555000000N``, email ``personaN@test.in``,
   password ``Test@1234``).
2. Creates each user's primary ``Portfolio``.
3. Inserts a static ``bank_account`` holding for the cash bucket and 2-4
   ``equity`` holdings per user matching their ``stock_picks`` in
   ``users.json``, weighted to ``stock_pct`` of net worth.
4. Calls ``reseed_dummy_mf_long_horizon._seed_one_user`` to fill in the MF
   ledger, holdings, daily history, allocations, and totals.

Journey lengths follow the persona index (1..10) — same as the previous
script:

    5550000001 = 1.0 yr  (Aarav, Aggressive, just started)
    5550000002 = 2.0 yr  (Priya)
    5550000003 = 3.5 yr  (Rohan)
    5550000004 = 5.0 yr  (Sneha)
    5550000005 = 6.0 yr  (Vikram)
    5550000006 = 7.0 yr  (Ananya)
    5550000007 = 8.0 yr  (Karthik)
    5550000008 = 8.5 yr  (Meera)
    5550000009 = 9.0 yr  (Arjun)
    5550000010 = 9.0 yr  (Divya)

Usage:
    python scripts/reset_dummy_users_long_horizon.py
"""
from __future__ import annotations

import asyncio
import json
import sys
import uuid
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import _get_session_factory, dispose_engine
from app.utils.security import hash_password

# Reuse the seeding logic from the long-horizon script
from scripts.reseed_dummy_mf_long_horizon import (  # type: ignore
    _seed_one_user,
    _journey_years_for,
)

DATA_DIR = Path(__file__).resolve().parent / "data"

NEW_PREFIX = "555000000"  # mobile = NEW_PREFIX + "1".."10"
NEW_MOBILES = [f"555000000{i}" if i < 10 else "5550000010" for i in range(1, 11)]
DEFAULT_PASSWORD = "Test@1234"


def _load_personas() -> list[dict]:
    """Load the 10 personas from users.json (ordered by mobile last-2-digits)."""
    with open(DATA_DIR / "users.json", encoding="utf-8") as f:
        rows = json.load(f)
    rows.sort(key=lambda r: int(r["mobile"][-2:]))
    if len(rows) != 10:
        raise RuntimeError(f"Expected 10 personas in users.json, got {len(rows)}")
    return rows


async def _wipe_users(db: AsyncSession) -> int:
    """Delete every users row. Cascade FKs handle dependent tables."""
    n = (await db.execute(text("SELECT COUNT(*) FROM users"))).scalar() or 0
    await db.execute(text("DELETE FROM users"))
    return int(n)


async def _create_user(db: AsyncSession, mobile: str, persona: dict) -> uuid.UUID:
    """Insert one fresh user keyed by ``mobile`` and return its id."""
    pw_hash = hash_password(DEFAULT_PASSWORD)
    new_email = f"{persona['first_name'].lower()}{mobile[-2:]}@dummy.in"
    new_pan = f"DUMMY{mobile[-5:]}X"  # short, unique-ish per mobile
    user_id = uuid.uuid4()
    dob_raw = persona.get("dob")
    dob = date.fromisoformat(dob_raw) if isinstance(dob_raw, str) else dob_raw

    await db.execute(text(
        "INSERT INTO users (id, email, country_code, mobile, phone, password_hash, "
        "  first_name, last_name, pan, date_of_birth, occupation, family_status, "
        "  currency, is_active, is_onboarding_complete, created_at, updated_at) "
        "VALUES (:id, :em, '+91', :mob, :ph, :pw, :fn, :ln, :pan, :dob, :occ, :fam, "
        "  'INR', true, true, now(), now())"
    ), {
        "id": user_id,
        "em": new_email,
        "mob": mobile,
        "ph": f"+91{mobile}",  # phone is unique
        "pw": pw_hash,
        "fn": persona.get("first_name"),
        "ln": persona.get("last_name"),
        "pan": new_pan,
        "dob": dob,
        "occ": persona.get("occupation"),
        "fam": persona.get("family_status"),
    })
    return user_id


async def _create_primary_portfolio(db: AsyncSession, user_id: uuid.UUID) -> uuid.UUID:
    pid = uuid.uuid4()
    await db.execute(text(
        "INSERT INTO portfolios (id, user_id, name, total_value, total_invested, "
        "  total_gain_percentage, is_primary, created_at, updated_at) "
        "VALUES (:id, :u, 'Primary', 0, 0, 0, true, now(), now())"
    ), {"id": pid, "u": user_id})
    return pid


async def _seed_non_mf_holdings(
    db: AsyncSession, portfolio_id: uuid.UUID, persona: dict
) -> None:
    """Insert a single bank_account row for cash and N equity rows for stock_picks.

    Allocation amounts come from users.json (cash_pct, stock_pct of net_worth).
    Equity rows use a synthetic NSE ticker name; quantities computed against a
    placeholder current_price so current_value = stock_amt / N for each pick.
    """
    nw = float(persona["net_worth"])
    cash_amt = round(nw * float(persona.get("cash_pct", 10)) / 100, 2)
    stock_amt = round(nw * float(persona.get("stock_pct", 20)) / 100, 2)

    if cash_amt > 0:
        await db.execute(text(
            "INSERT INTO portfolio_holdings (id, portfolio_id, instrument_name, "
            "  instrument_type, ticker_symbol, quantity, average_cost, current_price, "
            "  current_value, exchange, created_at, updated_at) "
            "VALUES (gen_random_uuid(), :p, :nm, 'bank_account', NULL, NULL, NULL, NULL, "
            "  :cv, NULL, now(), now())"
        ), {"p": portfolio_id, "nm": "Bank Deposits (1 accounts)", "cv": cash_amt})

    picks = persona.get("stock_picks") or []
    if picks and stock_amt > 0:
        per = round(stock_amt / len(picks), 2)
        # Placeholder market data — frontend can compute gain% from cost vs price.
        for sym in picks:
            cost = 100.0
            price = 110.0
            qty = round(per / price, 4)
            await db.execute(text(
                "INSERT INTO portfolio_holdings (id, portfolio_id, instrument_name, "
                "  instrument_type, ticker_symbol, quantity, average_cost, current_price, "
                "  current_value, exchange, created_at, updated_at) "
                "VALUES (gen_random_uuid(), :p, :nm, 'equity', :tk, :qty, :ac, :cp, :cv, "
                "  'NSE', now(), now())"
            ), {
                "p": portfolio_id, "nm": sym, "tk": sym,
                "qty": qty, "ac": cost, "cp": price, "cv": per,
            })


async def reset_and_seed() -> list[dict]:
    personas = _load_personas()
    factory = _get_session_factory()

    # ── 1. Wipe ──
    async with factory() as db:
        try:
            print("Wiping users table (CASCADE)... ", end="", flush=True)
            n = await _wipe_users(db)
            await db.commit()
            print(f"deleted {n} users")
        except Exception:
            await db.rollback()
            raise

    # ── 2. Create users + portfolios + non-MF holdings ──
    user_specs: list[dict] = []  # [{mobile, user_id, persona}]
    async with factory() as db:
        try:
            print(f"Creating 10 fresh users in series {NEW_PREFIX}1..{NEW_PREFIX[:-1]}10... ",
                  end="", flush=True)
            for mobile, persona in zip(NEW_MOBILES, personas):
                uid = await _create_user(db, mobile, persona)
                pid = await _create_primary_portfolio(db, uid)
                await _seed_non_mf_holdings(db, pid, persona)
                user_specs.append({"mobile": mobile, "user_id": uid, "persona": persona})
            await db.commit()
            print("OK")
        except Exception:
            await db.rollback()
            raise

    # ── 3. Apply long-horizon MF seed ──
    results: list[dict] = []
    for spec in user_specs:
        mobile = spec["mobile"]
        persona = spec["persona"]
        # _seed_one_user looks up the user by profile["mobile"]; clone with new mobile.
        local_profile = {**persona, "mobile": mobile}
        print(f"  Seeding MF for {mobile} ({persona['first_name']} {persona.get('last_name','')}, "
              f"journey={_journey_years_for(mobile)}y)... ", end="", flush=True)
        async with factory() as db:
            try:
                info = await _seed_one_user(db, local_profile)
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


def _print_summary(results: list[dict]) -> None:
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
    print("\nMobile numbers (serial — login with any of these, password=Test@1234):")
    for i, r in enumerate(results, 1):
        print(f"  {i:>2}. {r['mobile']}   ({r['name']}, {r['journey_yrs']:.1f}y horizon)")


if __name__ == "__main__":
    out = asyncio.run(reset_and_seed())
    _print_summary(out)
