"""Seed test users from JSON files in ./data/ directory.

Usage:
    cd Prozpr_Backend
    python scripts/seed_test_users.py          # seed users from data/users.json
    python scripts/seed_test_users.py --reset   # wipe existing test users first, then re-seed

Data files (edit these, then re-run):
    scripts/data/users.json      -- user personas (mobile, NW, allocations, stock picks, etc.)
    scripts/data/banks.json      -- bank catalog (name, IFSC)
    scripts/data/stocks.json     -- stock catalog (symbol, company, price)
    scripts/data/mf_funds.json   -- MF scheme catalog (scheme_code, name, category, etc.)

All users get password: Test@1234, country code: +91
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import _get_session_factory, create_all_tables, dispose_engine
from app.utils.security import hash_password, create_access_token

from app.models.user import User
from app.models.profile.personal_finance_profile import PersonalFinanceProfile
from app.models.profile.risk_profile import RiskProfile
from app.models.profile.investment_profile import InvestmentProfile
from app.models.profile.investment_constraint import InvestmentConstraint
from app.models.profile.asset_allocation_constraint import AssetAllocationConstraint
from app.models.profile.tax_profile import TaxProfile
from app.models.profile.review_preference import ReviewPreference
from app.models.linked_account import LinkedAccount, LinkedAccountType, LinkedAccountStatus
from app.models.portfolio import Portfolio, PortfolioAllocation, PortfolioHolding, PortfolioHistory
from app.models.goals.financial_goal import FinancialGoal
from app.models.goals.enums import GoalType, GoalStatus, GoalPriority
from app.models.goals.goal_contribution import GoalContribution
from app.models.mf.mf_fund_metadata import MfFundMetadata
from app.models.mf.enums import MfPlanType, MfOptionType, MfTransactionType, MfTransactionSource
from app.models.mf.mf_transaction import MfTransaction
from app.models.stocks.company_metadata import CompanyMetadata
from app.models.stocks.stock_transaction import StockTransaction
from app.models.stocks.enums import StockTransactionType
from app.models.notification import Notification

PASSWORD = "Test@1234"
COUNTRY_CODE = "+91"
NOW = datetime.now(timezone.utc)
TODAY = NOW.date()
DATA_DIR = Path(__file__).resolve().parent / "data"


def _load_json(filename: str) -> list[dict]:
    path = DATA_DIR / filename
    if not path.exists():
        print(f"ERROR: {path} not found")
        sys.exit(1)
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        print(f"ERROR: {path} must contain a JSON array")
        sys.exit(1)
    return data


def _user_uuid(mobile: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_DNS, f"prozpr-test-{mobile}")


async def _ensure_mf_metadata(db: AsyncSession, funds: list[dict]) -> None:
    for f in funds:
        existing = (await db.execute(
            select(MfFundMetadata).where(MfFundMetadata.scheme_code == f["scheme_code"])
        )).scalar_one_or_none()
        if existing:
            continue
        db.add(MfFundMetadata(
            scheme_code=f["scheme_code"],
            scheme_name=f["scheme_name"],
            amc_name=f["amc_name"],
            category=f["category"],
            sub_category=f.get("sub_category"),
            plan_type=MfPlanType(f.get("plan_type", "REGULAR")),
            option_type=MfOptionType(f.get("option_type", "GROWTH")),
            is_active=True,
        ))
    await db.flush()


async def _ensure_stock_metadata(db: AsyncSession, stocks: list[dict]) -> None:
    for s in stocks:
        existing = (await db.execute(
            select(CompanyMetadata).where(CompanyMetadata.symbol == s["symbol"])
        )).scalar_one_or_none()
        if existing:
            continue
        db.add(CompanyMetadata(
            symbol=s["symbol"],
            company_name=s["company_name"],
            exchange=s.get("exchange", "NSE"),
        ))
    await db.flush()


async def _delete_user_cascade(
    db: AsyncSession,
    user_id: uuid.UUID,
    mobile: str = "",
    email: str = "",
    pan: str = "",
) -> None:
    seen_ids: set[uuid.UUID] = set()
    candidates = []

    async def _try_add(user: User | None) -> None:
        if user and user.id not in seen_ids:
            seen_ids.add(user.id)
            candidates.append(user)

    await _try_add((await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none())
    if mobile:
        phone = f"{COUNTRY_CODE}{mobile}"
        await _try_add((await db.execute(select(User).where(User.phone == phone))).scalar_one_or_none())
        await _try_add((await db.execute(select(User).where(User.mobile == mobile))).scalar_one_or_none())
    if email:
        await _try_add((await db.execute(select(User).where(User.email == email))).scalar_one_or_none())
    if pan:
        await _try_add((await db.execute(select(User).where(User.pan == pan))).scalar_one_or_none())

    for u in candidates:
        await db.delete(u)
    if candidates:
        await db.flush()


async def _seed_one_user(
    db: AsyncSession,
    p: dict,
    banks: list[dict],
    stocks: list[dict],
    mf_funds: list[dict],
) -> dict:
    user_id = _user_uuid(p["mobile"])
    phone = f"{COUNTRY_CODE}{p['mobile']}"
    nw = p["net_worth"]
    income = p["annual_income"]

    await _delete_user_cascade(db, user_id, p["mobile"], p.get("email", ""), p.get("pan", ""))

    # ── User ──
    user = User(
        id=user_id,
        email=p.get("email"),
        country_code=COUNTRY_CODE,
        mobile=p["mobile"],
        phone=phone,
        password_hash=hash_password(PASSWORD),
        first_name=p["first_name"],
        last_name=p.get("last_name"),
        pan=p.get("pan"),
        date_of_birth=date.fromisoformat(p["dob"]) if p.get("dob") else None,
        occupation=p.get("occupation"),
        family_status=p.get("family_status"),
        currency="INR",
        is_active=True,
        is_onboarding_complete=True,
    )
    db.add(user)
    await db.flush()

    # ── PersonalFinanceProfile ──
    goal_type_str = p.get("goal_type", "OTHER")
    db.add(PersonalFinanceProfile(
        user_id=user_id,
        selected_goals=[goal_type_str],
        investment_horizon=p.get("horizon", "7-10 years"),
        annual_income_min=income * 0.8,
        annual_income_max=income * 1.2,
        annual_expense_min=income * 0.4,
        annual_expense_max=income * 0.6,
        wealth_sources=["Salary", "Investments"],
    ))

    # ── RiskProfile ──
    risk_level = p.get("risk_level", 3)
    db.add(RiskProfile(
        user_id=user_id,
        risk_level=risk_level,
        risk_capacity=p.get("risk_label", "Moderate"),
        investment_experience="5+ years" if risk_level >= 3 else "1-3 years",
        investment_horizon=p.get("horizon", "7-10 years"),
        drop_reaction="Hold and wait" if risk_level >= 3 else "Sell partially",
        max_drawdown=float(risk_level * 10),
        comfort_assets=["Equity", "Debt", "Gold"],
    ))

    # ── InvestmentProfile ──
    monthly_savings = round(income * 0.25 / 12, 2)
    goal_amount = p.get("goal_amount", nw * 3)
    db.add(InvestmentProfile(
        user_id=user_id,
        objectives=[goal_type_str],
        portfolio_value=float(nw),
        monthly_savings=monthly_savings,
        target_corpus=float(goal_amount),
        target_timeline=p.get("horizon", "7-10 years"),
        annual_income=float(income),
        retirement_age=60,
        investable_assets=float(nw * 0.8),
        total_liabilities=float(nw * 0.1),
        emergency_fund=float(income * 0.5),
        emergency_fund_months="6 months",
        liquidity_needs="Low" if risk_level >= 3 else "Moderate",
        total_horizon=p.get("horizon", "7-10 years"),
    ))

    # ── InvestmentConstraint ──
    ic = InvestmentConstraint(
        user_id=user_id,
        permitted_assets=["Equity", "Debt", "Gold", "International Equity"],
        prohibited_instruments=["Cryptocurrency"],
        is_leverage_allowed=False,
        is_derivatives_allowed=False,
    )
    db.add(ic)
    await db.flush()

    total_equity_pct = p.get("mf_pct", 40) + p.get("stock_pct", 20)
    for ac_data in [
        {"asset_class": "Equity", "min_allocation": max(total_equity_pct - 15, 10), "max_allocation": min(total_equity_pct + 15, 90)},
        {"asset_class": "Debt",   "min_allocation": max(p.get("debt_pct", 20) - 10, 5), "max_allocation": min(p.get("debt_pct", 20) + 10, 80)},
        {"asset_class": "Cash",   "min_allocation": 5, "max_allocation": min(p.get("cash_pct", 10) + 10, 30)},
        {"asset_class": "Other",  "min_allocation": 0, "max_allocation": 20},
    ]:
        db.add(AssetAllocationConstraint(constraint_id=ic.id, **ac_data))

    # ── TaxProfile ──
    if income > 15_00_000:
        tax_rate = 30.0
    elif income > 10_00_000:
        tax_rate = 20.0
    elif income > 5_00_000:
        tax_rate = 10.0
    else:
        tax_rate = 5.0
    db.add(TaxProfile(user_id=user_id, income_tax_rate=tax_rate, capital_gains_tax_rate=15.0))

    # ── ReviewPreference ──
    db.add(ReviewPreference(
        user_id=user_id,
        frequency="Quarterly",
        triggers=["Market drop > 10%", "Goal milestone reached"],
        update_process="Review with advisor",
    ))

    # ── NW breakdown ──
    cash_pct  = p.get("cash_pct", 10)
    mf_pct    = p.get("mf_pct", 40)
    stock_pct = p.get("stock_pct", 20)
    debt_pct  = p.get("debt_pct", 20)
    other_pct = p.get("other_pct", 10)

    cash_amt  = round(nw * cash_pct / 100, 2)
    mf_amt    = round(nw * mf_pct / 100, 2)
    stock_amt = round(nw * stock_pct / 100, 2)
    debt_amt  = round(nw * debt_pct / 100, 2)
    other_amt = round(nw * other_pct / 100, 2)
    total_invested = round(nw * 0.85, 2)
    gain_pct = round((nw - total_invested) / total_invested * 100, 2) if total_invested > 0 else None

    # ── Linked Bank Accounts ──
    num_banks = min(p.get("num_banks", 1), len(banks))
    user_banks = banks[:num_banks]
    remaining_cash = cash_amt
    for bi, bank in enumerate(user_banks):
        if bi == len(user_banks) - 1:
            bal = remaining_cash
        else:
            bal = round(remaining_cash * 0.55, 2)
            remaining_cash -= bal
        masked = f"XXXXX{hash(bank['name'] + p['mobile']) % 9000 + 1000}"
        db.add(LinkedAccount(
            user_id=user_id,
            account_type=LinkedAccountType.bank_account,
            provider_name=bank["name"],
            account_identifier=f"{bank['ifsc']}-{p['mobile'][-4:]}",
            status=LinkedAccountStatus.active,
            metadata_json={
                "fi_type": "DEPOSIT", "account_type": "SAVINGS",
                "portfolio_bucket": "Cash", "masked_acc_number": masked,
                "ifsc_code": bank["ifsc"], "current_balance": bal,
            },
            linked_at=NOW, last_synced_at=NOW,
        ))

    # ── Linked MF Account ──
    db.add(LinkedAccount(
        user_id=user_id,
        account_type=LinkedAccountType.mutual_fund,
        provider_name="CAMS / KFintech",
        account_identifier=f"MF-{p.get('pan', p['mobile'])}",
        status=LinkedAccountStatus.active,
        metadata_json={
            "fi_type": "MFC_MF", "account_type": "DEFAULT",
            "portfolio_bucket": "MixedMF",
            "masked_folio_no": f"FOLIO-{p['mobile'][-4:]}",
            "cost_value": round(mf_amt * 0.88, 2), "current_value": mf_amt,
        },
        linked_at=NOW, last_synced_at=NOW,
    ))

    # ── Linked Demat Account ──
    stock_picks = p.get("stock_picks", [])
    stock_lookup = {s["symbol"]: s for s in stocks}
    if stock_amt > 0 and stock_picks:
        db.add(LinkedAccount(
            user_id=user_id,
            account_type=LinkedAccountType.stock_demat,
            provider_name="Zerodha",
            account_identifier=f"ZRD-{p['mobile'][-4:]}",
            status=LinkedAccountStatus.active,
            metadata_json={
                "fi_type": "EQUITIES", "account_type": "DEFAULT",
                "portfolio_bucket": "Equity",
                "masked_demat_id": f"XXXXXXXXXXXX{p['mobile'][-4:]}",
                "current_value": stock_amt,
            },
            linked_at=NOW, last_synced_at=NOW,
        ))

    # ── Portfolio ──
    equity_total = mf_amt + stock_amt
    portfolio = Portfolio(
        user_id=user_id, name="Primary",
        total_value=float(nw), total_invested=total_invested,
        total_gain_percentage=gain_pct, is_primary=True,
    )
    db.add(portfolio)
    await db.flush()

    for bucket, amt in [("Cash", cash_amt), ("Equity", equity_total), ("Debt", debt_amt), ("Other", other_amt)]:
        if amt <= 0:
            continue
        db.add(PortfolioAllocation(
            portfolio_id=portfolio.id, asset_class=bucket,
            allocation_percentage=round(amt / nw * 100, 2), amount=amt,
        ))

    # ── MF Holdings ──
    eq_funds   = [f for f in mf_funds if f["category"] == "Equity"]
    debt_funds = [f for f in mf_funds if f["category"] == "Debt"]
    oth_funds  = [f for f in mf_funds if f["category"] == "Other"]

    mf_eq_amt  = round(mf_amt * 0.65, 2)
    mf_dbt_amt = round(mf_amt * 0.25, 2)
    mf_oth_amt = round(mf_amt * 0.10, 2)

    num_eq = min(max(1, int(mf_eq_amt / 200000) + 1), len(eq_funds))
    selected_eq = eq_funds[:num_eq]
    fund_allocs: list[tuple[dict, float]] = []
    eq_remaining = mf_eq_amt
    for fi, fund in enumerate(selected_eq):
        if fi == len(selected_eq) - 1:
            fund_allocs.append((fund, eq_remaining))
        else:
            share = round(mf_eq_amt / len(selected_eq), 2)
            fund_allocs.append((fund, share))
            eq_remaining -= share
    if debt_funds:
        fund_allocs.append((debt_funds[0], mf_dbt_amt))
    if oth_funds and mf_oth_amt > 0:
        fund_allocs.append((oth_funds[0], mf_oth_amt))

    for fund, amt in fund_allocs:
        if amt <= 0:
            continue
        nav = 50.0 + (hash(fund["scheme_code"]) % 200)
        qty = round(amt / nav, 4)
        db.add(PortfolioHolding(
            portfolio_id=portfolio.id,
            instrument_name=fund["scheme_name"], instrument_type="mutual_fund",
            ticker_symbol=fund["scheme_code"][:20],
            quantity=qty, average_cost=round(nav * 0.9, 2),
            current_price=nav, current_value=amt,
        ))

    # ── Stock Holdings + Transactions ──
    if stock_picks and stock_amt > 0:
        per_stock = round(stock_amt / len(stock_picks), 2)
        for si, sym in enumerate(stock_picks):
            s = stock_lookup.get(sym)
            if not s:
                continue
            s_amt = per_stock if si < len(stock_picks) - 1 else round(stock_amt - per_stock * si, 2)
            price = s["price"]
            qty = round(s_amt / price, 4)
            avg_cost = round(price * 0.88, 2)
            db.add(PortfolioHolding(
                portfolio_id=portfolio.id,
                instrument_name=s["company_name"], instrument_type="equity",
                ticker_symbol=sym[:20],
                quantity=qty, average_cost=avg_cost,
                current_price=price, current_value=round(qty * price, 2),
                exchange=s.get("exchange", "NSE"),
            ))
            for months_ago in [9, 3]:
                txn_date = TODAY - timedelta(days=months_ago * 30)
                txn_qty = round(qty / 2, 4)
                db.add(StockTransaction(
                    user_id=user_id, symbol=sym,
                    transaction_type=StockTransactionType.BUY,
                    transaction_date=txn_date,
                    quantity=txn_qty, price=avg_cost,
                    amount=round(txn_qty * avg_cost, 2),
                ))

    # ── Cash Holding ──
    if cash_amt > 0:
        db.add(PortfolioHolding(
            portfolio_id=portfolio.id,
            instrument_name=f"Bank Deposits ({num_banks} accounts)",
            instrument_type="bank_account",
            quantity=None, average_cost=None, current_price=None,
            current_value=cash_amt,
        ))

    # ── Portfolio History (90 days weekly) ──
    for days_ago in range(90, -1, -7):
        hist_date = TODAY - timedelta(days=days_ago)
        growth_factor = 1 + (90 - days_ago) / 90 * 0.15 * (risk_level / 3)
        db.add(PortfolioHistory(
            portfolio_id=portfolio.id,
            recorded_date=hist_date,
            total_value=round(nw / growth_factor, 2),
        ))

    # ── Goals ──
    goal = FinancialGoal(
        user_id=user_id,
        goal_name=p.get("goal_name", "Financial Goal"),
        goal_type=GoalType(goal_type_str),
        present_value_amount=float(goal_amount),
        inflation_rate=6.0,
        target_date=TODAY + timedelta(days=p.get("goal_years", 10) * 365),
        priority=GoalPriority.PRIMARY,
        status=GoalStatus.ACTIVE,
    )
    db.add(goal)
    await db.flush()
    db.add(GoalContribution(goal_id=goal.id, amount=round(goal_amount * 0.1, 2), note="Initial contribution"))

    if nw >= 50_00_000:
        g2 = FinancialGoal(
            user_id=user_id, goal_name="Emergency Fund",
            goal_type=GoalType.EMERGENCY_FUND,
            present_value_amount=float(income * 0.5), inflation_rate=6.0,
            target_date=TODAY + timedelta(days=365),
            priority=GoalPriority.MEDIUM, status=GoalStatus.ACTIVE,
        )
        db.add(g2)
        await db.flush()
        db.add(GoalContribution(goal_id=g2.id, amount=round(income * 0.1, 2), note="Monthly auto"))

    # ── MF Transactions ──
    for fund, amt in fund_allocs:
        if amt <= 0:
            continue
        nav = 50.0 + (hash(fund["scheme_code"]) % 200)
        for months_ago in [12, 6, 1]:
            txn_date = TODAY - timedelta(days=months_ago * 30)
            txn_amt = round(amt / 3, 2)
            units = round(txn_amt / nav, 4)
            fingerprint = sha256(
                f"{user_id}|{fund['scheme_code']}|FOLIO-{p['mobile'][-4:]}|BUY|{txn_date}|{units}|{nav}|{txn_amt}".encode()
            ).hexdigest()
            db.add(MfTransaction(
                user_id=user_id, scheme_code=fund["scheme_code"],
                folio_number=f"FOLIO-{p['mobile'][-4:]}",
                transaction_type=MfTransactionType.BUY,
                transaction_date=txn_date, units=units, nav=nav, amount=txn_amt,
                source_system=MfTransactionSource.MANUAL,
                source_txn_fingerprint=fingerprint,
            ))

    # ── Notifications ──
    db.add(Notification(
        user_id=user_id, title="Welcome to Prozpr!",
        message=f"Hi {p['first_name']}, your portfolio of Rs.{nw/100000:.1f}L is ready for review.",
        notification_type="info", is_read=False,
    ))
    db.add(Notification(
        user_id=user_id, title="Quarterly Review Due",
        message="Your next portfolio review is scheduled. Tap to start.",
        notification_type="reminder", is_read=False,
    ))

    await db.flush()
    token = create_access_token(user_id, phone)
    return {"mobile": p["mobile"], "name": f"{p['first_name']} {p.get('last_name', '')}", "net_worth": nw, "token": token}


async def seed_one(mobile: str, reset: bool = False) -> None:
    """Seed a single user by mobile number. Fresh DB connection per call."""
    users    = _load_json("users.json")
    banks    = _load_json("banks.json")
    stocks   = _load_json("stocks.json")
    mf_funds = _load_json("mf_funds.json")

    p = next((u for u in users if u["mobile"] == mobile), None)
    if not p:
        print(f"ERROR: mobile {mobile} not found in users.json")
        sys.exit(1)

    await create_all_tables()
    factory = _get_session_factory()
    async with factory() as db:
        try:
            if reset:
                await _delete_user_cascade(db, _user_uuid(mobile), mobile, p.get("email", ""), p.get("pan", ""))
                await db.commit()

            await _ensure_mf_metadata(db, mf_funds)
            await _ensure_stock_metadata(db, stocks)
            await db.commit()

            info = await _seed_one_user(db, p, banks, stocks, mf_funds)
            await db.commit()

            print(f"OK  {info['mobile']}  {info['name']:<20}  Rs.{info['net_worth']/100000:.0f}L  token={info['token'][:50]}...")
        except Exception as exc:
            await db.rollback()
            print(f"FAIL {mobile}: {exc}")
            sys.exit(1)
        finally:
            await dispose_engine()


async def seed_all(reset: bool = False) -> None:
    """Seed all users one at a time with a fresh DB session per user."""
    users    = _load_json("users.json")
    banks    = _load_json("banks.json")
    stocks   = _load_json("stocks.json")
    mf_funds = _load_json("mf_funds.json")

    print(f"Loaded: {len(users)} users, {len(banks)} banks, {len(stocks)} stocks, {len(mf_funds)} MF funds")
    print(f"Data dir: {DATA_DIR}\n")

    await create_all_tables()

    # Ensure catalogs first (separate session)
    factory = _get_session_factory()
    async with factory() as db:
        await _ensure_mf_metadata(db, mf_funds)
        await _ensure_stock_metadata(db, stocks)
        await db.commit()
    await dispose_engine()
    print("Catalogs ready (MF funds + stocks).\n")

    results = []
    for i, p in enumerate(users, 1):
        mobile = p["mobile"]
        nw_label = f"Rs.{p['net_worth']/100000:.0f}L"
        print(f"[{i}/{len(users)}] {p['first_name']} {p.get('last_name','')} ({mobile}) -- {nw_label} ... ", end="", flush=True)

        # Fresh engine + session per user to avoid RDS connection drops
        factory = _get_session_factory()
        async with factory() as db:
            try:
                if reset:
                    await _delete_user_cascade(db, _user_uuid(mobile), mobile, p.get("email", ""), p.get("pan", ""))
                    await db.commit()

                info = await _seed_one_user(db, p, banks, stocks, mf_funds)
                await db.commit()
                results.append(info)
                print("OK")
            except Exception as exc:
                await db.rollback()
                print(f"FAIL: {exc}")
        await dispose_engine()

    print("\n" + "=" * 75)
    print(f"SEEDED: {len(results)}/{len(users)} users")
    print("=" * 75)
    print(f"{'Mobile':<14} {'Name':<20} {'Net Worth':<12} {'Password'}")
    print("-" * 75)
    for r in results:
        print(f"{r['mobile']:<14} {r['name']:<20} Rs.{r['net_worth']/100000:.0f}L{'':<8} {PASSWORD}")
    print("-" * 75)
    print(f"\nPassword for all: {PASSWORD}  |  Country code: {COUNTRY_CODE}")
    print(f"To update data: edit files in {DATA_DIR}/ and re-run.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed test users from scripts/data/*.json into Prozpr DB")
    parser.add_argument("--reset", action="store_true", help="Delete existing test users before seeding")
    parser.add_argument("--mobile", type=str, default=None, help="Seed only this mobile number (e.g. 9000000001)")
    args = parser.parse_args()

    if args.mobile:
        asyncio.run(seed_one(args.mobile, reset=args.reset))
    else:
        asyncio.run(seed_all(reset=args.reset))
