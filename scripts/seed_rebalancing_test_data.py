"""DEV-ONLY: seed reconciled rebalancing test data for the 8888888 profiles.

User 8888888881 already has six ``portfolio_holdings`` rows with synthetic
ticker_symbols (HDFCFLX, MIRAELC, ...). This script:

1. Wipes that user's existing 2,244 synthetic ``mf_transactions`` rows.
2. For each of the 6 holdings, generates two BUY lots whose total units +
   total invested cost reconcile exactly with ``portfolio_holdings.quantity``
   and ``portfolio_holdings.average_cost``. 60% units land on a long-term
   acquisition date (>2 years ago), 40% on a short-term date (<3 months).
3. Seeds ``mf_nav_history`` with a recent NAV per scheme matching
   ``portfolio_holdings.current_price`` so the engine's ``current_nav`` lines
   up with the displayed market value.
4. Enriches ``mf_fund_metadata`` with ``asset_class`` / ``asset_subgroup`` /
   ``sub_category`` / ``exit_load_*`` so tax-aging and exit-load math run.
5. For user 8888888882 (currently empty), seeds two holdings + reconciled
   lots: one recommended (rank-1 of low_beta_equities, in the fund-rank
   CSV) plus one BAD fund — exercising both engine branches.
6. Seeds ``tax_profiles`` rows for both users with new-regime defaults.

ISIN choices: user 1's existing instruments are NOT in the fund-rank CSV,
so they all surface as BAD funds — realistic, and exercises the exit path.
User 2's recommended pick uses INF209K01YY7 (Aditya Birla SL Large Cap,
rank-1 in CSV).

Idempotent: re-running deletes seeded txns + NAVs first.
"""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

DB = Path(__file__).resolve().parents[1] / "wealth_agent.db"

USER1 = "70a4b95457004cf5a99409df410203ae"  # 8888888881
USER2 = "a6927ae8fe6e45ceb025fabc5f1fa9a2"  # 8888888882

LT_DATE = date(2023, 4, 1)   # >2 years ago — LT for both equity (12mo) and debt (24mo)
ST_DATE = date(2026, 2, 15)  # ~2.5 months ago — ST for both
TODAY = date(2026, 4, 29)


@dataclass(frozen=True)
class Holding:
    user_id: str
    scheme_code: str
    isin: str
    instrument_name: str
    amc_name: str
    quantity: Decimal       # remaining FIFO units (= portfolio_holdings.quantity)
    avg_cost: Decimal       # weighted avg acquisition NAV
    current_nav: Decimal    # latest mf_nav_history NAV
    asset_class: str        # 'equity' | 'debt' | 'others'
    asset_subgroup: str     # CSV asset_subgroup label
    sub_category: str       # SEBI sub_category
    exit_load_pct: Decimal
    exit_load_months: int


# ── User 1: 6 holdings — 4 mapped to recommended CSV ISINs, 2 stay BAD.
#
# Realistic mix: legacy holdings should mostly overlap with the recommended
# list (so the engine confirms "hold/redistribute") with a few outliers the
# rebalancer would flag for exit. Mapping rationale:
#  - Flexi Cap → Bajaj Finserv Flexi Cap (rank 5 medium_beta_equities)
#  - Large Cap → Bandhan Large Cap (rank 4 low_beta_equities)
#  - Midcap   → BAD: CSV has no Mid Cap sub_category at all
#  - Corp Bond → BAD: CSV has no Corporate Bond sub_category
#  - Short Term → ABSL Savings (rank 1 short_debt Ultra Short Duration; closest fit)
#  - Liquid → Axis Liquid (rank 4 debt_subgroup Liquid Fund)
USER1_HOLDINGS = [
    Holding(USER1, "BAJ_FLX_DG", "INF0QA701342",
            "Bajaj Finserv Flexi Cap Fund-Direct Plan-Growth",
            "Bajaj Finserv Mutual Fund", Decimal("952.38"), Decimal("1750"),
            Decimal("2100"), "equity", "medium_beta_equities", "Flexi Cap Fund",
            Decimal("1.0"), 12),
    Holding(USER1, "BANDHAN_LC_DG", "INF194K01Z44",
            "BANDHAN Large Cap Fund-Direct Plan-Growth",
            "Bandhan Mutual Fund", Decimal("12500"), Decimal("100"),
            Decimal("120"), "equity", "low_beta_equities", "Large Cap Fund",
            Decimal("1.0"), 12),
    Holding(USER1, "AXISMID", "INF00MID001",
            "Axis Midcap Fund - Direct Plan Growth",
            "Axis Mutual Fund", Decimal("10909.09"), Decimal("95"),
            Decimal("110"), "equity", "high_beta_equities", "Mid Cap Fund",
            Decimal("1.0"), 12),
    Holding(USER1, "HDFCCB", "INF00CRP001",
            "HDFC Corporate Bond Fund - Direct Plan Growth",
            "HDFC Mutual Fund", Decimal("60000"), Decimal("28"),
            Decimal("30"), "debt", "debt_subgroup", "Corporate Bond Fund",
            Decimal("0.5"), 6),
    Holding(USER1, "ABSL_SAV_DG", "INF209K01UR9",
            "Aditya Birla Sun Life Savings Fund - Growth - Direct Plan",
            "Aditya Birla Sun Life Mutual Fund", Decimal("40000"), Decimal("23"),
            Decimal("25"), "debt", "short_debt", "Ultra Short Duration Fund",
            Decimal("0.25"), 6),
    Holding(USER1, "AXIS_LIQ_DG", "INF846K01CX4",
            "Axis Liquid Fund - Direct Plan - Growth Option",
            "Axis Mutual Fund", Decimal("104.17"), Decimal("4600"),
            Decimal("4800"), "debt", "debt_subgroup", "Liquid Fund",
            Decimal("0.0"), 0),
]

# ── User 2: clean slate, 2 holdings — one recommended + one BAD ──
USER2_HOLDINGS = [
    # Recommended: rank-1 of low_beta_equities in the fund-rank CSV.
    Holding(USER2, "ABSL_LC_DG", "INF209K01YY7",
            "Aditya Birla Sun Life Large Cap Fund - Growth - Direct Plan",
            "Aditya Birla Sun Life Mutual Fund", Decimal("1500"), Decimal("200"),
            Decimal("220"), "equity", "low_beta_equities", "Large Cap Fund",
            Decimal("1.0"), 12),
    # BAD: not in CSV — surfaces as a fund the engine would suggest exiting.
    Holding(USER2, "MYELS_DG", "INF99TST001",
            "Mystery Tactical Equity Fund - Direct Plan Growth",
            "Mystery AMC", Decimal("1000"), Decimal("50"), Decimal("55"),
            "equity", "value_equities", "Value Fund", Decimal("1.0"), 12),
]


def _quantize(d: Decimal) -> Decimal:
    return d.quantize(Decimal("0.0001"))


def _money(d: Decimal) -> Decimal:
    return d.quantize(Decimal("0.01"))


def _lot_pair(h: Holding) -> tuple[tuple[Decimal, Decimal, date], tuple[Decimal, Decimal, date]]:
    """Two buy lots whose totals reconcile with quantity × avg_cost.

    Mix:
      - LT: 60% units × 0.95 × avg_cost
      - ST: 40% units × 1.075 × avg_cost
    Sum = qty × avg_cost × (0.6 × 0.95 + 0.4 × 1.075) = qty × avg_cost × 1.0
    """
    lt_units = _quantize(h.quantity * Decimal("0.6"))
    st_units = _quantize(h.quantity - lt_units)  # exact remainder, no drift
    lt_nav = _quantize(h.avg_cost * Decimal("0.95"))
    # ST NAV computed from totals so the math is exact even after rounding.
    target_total = h.quantity * h.avg_cost
    lt_cost = lt_units * lt_nav
    st_cost = target_total - lt_cost
    st_nav = _quantize(st_cost / st_units) if st_units > 0 else Decimal(0)
    return (lt_units, lt_nav, LT_DATE), (st_units, st_nav, ST_DATE)


def _ensure_metadata(cur: sqlite3.Cursor, h: Holding) -> None:
    cur.execute(
        "SELECT id FROM mf_fund_metadata WHERE scheme_code = ?", (h.scheme_code,),
    )
    row = cur.fetchone()
    if row is None:
        cur.execute(
            """
            INSERT INTO mf_fund_metadata
            (id, scheme_code, scheme_name, amc_name, category, sub_category,
             plan_type, option_type, is_active, asset_class, asset_subgroup,
             exit_load_percent, exit_load_months)
            VALUES (?, ?, ?, ?, ?, ?, 'DIRECT', 'GROWTH', 1, ?, ?, ?, ?)
            """,
            (
                uuid.uuid4().hex, h.scheme_code, h.instrument_name, h.amc_name,
                "Equity" if h.asset_class == "equity" else "Debt",
                h.sub_category, h.asset_class, h.asset_subgroup,
                str(h.exit_load_pct), h.exit_load_months,
            ),
        )
    else:
        cur.execute(
            """
            UPDATE mf_fund_metadata
               SET scheme_name=?, amc_name=?, sub_category=?,
                   asset_class=?, asset_subgroup=?, exit_load_percent=?,
                   exit_load_months=?, is_active=1
             WHERE scheme_code=?
            """,
            (
                h.instrument_name, h.amc_name, h.sub_category,
                h.asset_class, h.asset_subgroup, str(h.exit_load_pct),
                h.exit_load_months, h.scheme_code,
            ),
        )


def _ensure_nav(cur: sqlite3.Cursor, h: Holding) -> None:
    """Latest NAV at TODAY matching portfolio_holdings.current_price.

    Also wipes any other scheme_code rows pointing at the same ISIN so the
    input_builder's per-ISIN dedupe sees exactly one NAV (avoids tied
    nav_date rows that resolve in undefined order on SQLite).
    """
    cur.execute("DELETE FROM mf_nav_history WHERE scheme_code = ?", (h.scheme_code,))
    cur.execute("DELETE FROM mf_nav_history WHERE isin = ?", (h.isin,))
    cur.execute(
        """
        INSERT INTO mf_nav_history
        (id, scheme_code, isin, scheme_name, mf_type, nav, nav_date)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            uuid.uuid4().hex, h.scheme_code, h.isin, h.instrument_name,
            "EQUITY" if h.asset_class == "equity" else "DEBT",
            str(h.current_nav), TODAY.isoformat(),
        ),
    )


def _seed_lots(cur: sqlite3.Cursor, h: Holding) -> None:
    (lt_units, lt_nav, lt_date), (st_units, st_nav, st_date) = _lot_pair(h)
    folio = f"FOLIO-{h.scheme_code}"
    for units, nav, txn_date in [(lt_units, lt_nav, lt_date), (st_units, st_nav, st_date)]:
        cur.execute(
            """
            INSERT INTO mf_transactions
            (id, user_id, scheme_code, folio_number, transaction_type,
             transaction_date, units, nav, amount, source_system)
            VALUES (?, ?, ?, ?, 'BUY', ?, ?, ?, ?, 'MANUAL')
            """,
            (
                uuid.uuid4().hex, h.user_id, h.scheme_code, folio,
                txn_date.isoformat(), str(units), str(nav),
                str(_money(units * nav)),
            ),
        )


def _seed_tax_profile(
    cur: sqlite3.Cursor, user_id: str, *, regime: str, rate: int,
) -> None:
    cur.execute("SELECT id FROM tax_profiles WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    if row is None:
        cur.execute(
            """
            INSERT INTO tax_profiles
            (id, user_id, income_tax_rate, capital_gains_tax_rate,
             tax_regime, carryforward_st_loss_inr, carryforward_lt_loss_inr)
            VALUES (?, ?, ?, ?, ?, 0, 0)
            """,
            (
                uuid.uuid4().hex, user_id, rate, 20.0, regime,
            ),
        )
    else:
        cur.execute(
            """
            UPDATE tax_profiles
               SET income_tax_rate=?, capital_gains_tax_rate=?, tax_regime=?,
                   carryforward_st_loss_inr=0, carryforward_lt_loss_inr=0
             WHERE user_id=?
            """,
            (rate, 20.0, regime, user_id),
        )


def _rewrite_portfolio_for(
    cur: sqlite3.Cursor, user_id: str, holdings: list[Holding],
) -> None:
    """(Re)create a Primary Portfolio + holdings + allocations matching ``holdings``.

    Replaces any existing portfolio_holdings / portfolio_allocations under
    the user's primary portfolio so name/ticker/quantity/value stay in sync
    with the seeded transactions. NAV math: current_value = qty × current_nav.
    """
    cur.execute("SELECT id FROM portfolios WHERE user_id=? AND is_primary=1", (user_id,))
    row = cur.fetchone()
    total_value = sum(h.quantity * h.current_nav for h in holdings)
    total_invested = sum(h.quantity * h.avg_cost for h in holdings)
    gain_pct = ((Decimal(total_value) - Decimal(total_invested))
                / Decimal(total_invested) * 100) if total_invested else Decimal(0)
    if row is None:
        portfolio_id = uuid.uuid4().hex
        cur.execute(
            """
            INSERT INTO portfolios
            (id, user_id, name, total_value, total_invested, total_gain_percentage,
             is_primary)
            VALUES (?, ?, 'Primary Portfolio', ?, ?, ?, 1)
            """,
            (
                portfolio_id, user_id, str(_money(Decimal(total_value))),
                str(_money(Decimal(total_invested))), str(_money(gain_pct)),
            ),
        )
    else:
        portfolio_id = row[0]
        cur.execute(
            """
            UPDATE portfolios SET total_value=?, total_invested=?, total_gain_percentage=?
             WHERE id=?
            """,
            (
                str(_money(Decimal(total_value))),
                str(_money(Decimal(total_invested))),
                str(_money(gain_pct)),
                portfolio_id,
            ),
        )

    cur.execute("DELETE FROM portfolio_holdings WHERE portfolio_id=?", (portfolio_id,))
    cur.execute("DELETE FROM portfolio_allocations WHERE portfolio_id=?", (portfolio_id,))

    for h in holdings:
        cur.execute(
            """
            INSERT INTO portfolio_holdings
            (id, portfolio_id, instrument_name, instrument_type, ticker_symbol,
             quantity, average_cost, current_price, current_value)
            VALUES (?, ?, ?, 'mutual_fund', ?, ?, ?, ?, ?)
            """,
            (
                uuid.uuid4().hex, portfolio_id, h.instrument_name, h.scheme_code,
                str(h.quantity), str(h.avg_cost), str(h.current_nav),
                str(_money(h.quantity * h.current_nav)),
            ),
        )

    by_cls: dict[str, Decimal] = {}
    for h in holdings:
        by_cls[h.asset_class] = by_cls.get(h.asset_class, Decimal(0)) + (
            h.quantity * h.current_nav
        )
    total = sum(by_cls.values())
    label = {"equity": "Equity", "debt": "Debt", "others": "Others"}
    for cls, amount in by_cls.items():
        cur.execute(
            """
            INSERT INTO portfolio_allocations
            (id, portfolio_id, asset_class, allocation_percentage, amount)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                uuid.uuid4().hex, portfolio_id, label[cls],
                str(_money(amount / total * 100)),
                str(_money(amount)),
            ),
        )


def _seed_all_csv_fund_navs(cur: sqlite3.Cursor) -> int:
    """Seed minimal MfFundMetadata + MfNavHistory for every fund-rank CSV ISIN.

    The rebalancing input_builder prices every recommended ISIN, so the dev
    DB needs a NAV for each — even ones no test user holds. Default NAV is
    ₹100 at TODAY; the engine just needs *something* to convert rupees to
    units. Idempotent.
    """
    import csv

    csv_path = Path(__file__).resolve().parents[1] / "AI_Agents" / "Reference_docs" / "Prozpr_fund_ranking.csv"
    seeded = 0
    sub_cat_to_class: dict[str, str] = {
        "Large Cap Fund": "equity", "Flexi Cap Fund": "equity",
        "Multi Cap Fund": "equity", "Small Cap Fund": "equity",
        "Value Fund": "equity", "Dividend Yield Fund": "equity",
        "ELSS": "equity", "Sectoral/ Thematic": "equity",
        "Index Funds": "equity", "Liquid Fund": "debt",
        "Ultra Short Duration Fund": "debt", "Low Duration Fund": "debt",
        "Arbitrage Fund": "debt", "FoF Domestic": "others",
        "FoF Overseas": "others", "Gold ETF": "others",
        "Other  ETFs": "others",
    }
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            isin = row["isin"]
            sub_category = row["sub_category"]
            asset_subgroup = row["asset_subgroup"]
            fund_name = row["recommended_fund"]
            scheme_code = f"CSV_{isin}"
            asset_class = sub_cat_to_class.get(sub_category, "equity")
            # Skip if a real holding already owns this ISIN — avoids stomping
            # on the explicit user-2 rank-1 row, which has its own scheme_code.
            cur.execute(
                "SELECT scheme_code FROM mf_nav_history WHERE isin=?", (isin,),
            )
            existing = cur.fetchone()
            if existing is not None:
                continue
            cur.execute(
                "SELECT id FROM mf_fund_metadata WHERE scheme_code = ?",
                (scheme_code,),
            )
            if cur.fetchone() is None:
                cur.execute(
                    """
                    INSERT INTO mf_fund_metadata
                    (id, scheme_code, scheme_name, amc_name, category, sub_category,
                     plan_type, option_type, is_active, asset_class, asset_subgroup,
                     exit_load_percent, exit_load_months)
                    VALUES (?, ?, ?, 'Recommended AMC', ?, ?, 'DIRECT', 'GROWTH', 1,
                            ?, ?, ?, ?)
                    """,
                    (
                        uuid.uuid4().hex, scheme_code, fund_name,
                        "Equity" if asset_class == "equity" else "Debt",
                        sub_category, asset_class, asset_subgroup,
                        "1.0" if asset_class == "equity" else "0.0",
                        12 if asset_class == "equity" else 0,
                    ),
                )
            cur.execute(
                """
                INSERT INTO mf_nav_history
                (id, scheme_code, isin, scheme_name, mf_type, nav, nav_date)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    uuid.uuid4().hex, scheme_code, isin, fund_name,
                    "EQUITY" if asset_class == "equity" else "DEBT",
                    "100.0", TODAY.isoformat(),
                ),
            )
            seeded += 1
    return seeded


def main() -> None:
    con = sqlite3.connect(DB)
    cur = con.cursor()

    # 1. Wipe user 1's synthetic mf_transactions.
    cur.execute("DELETE FROM mf_transactions WHERE user_id = ?", (USER1,))
    print(f"deleted {cur.rowcount} synthetic txns for user 8888888881")

    # 2. Wipe user 2's mf_transactions (in case re-running).
    cur.execute("DELETE FROM mf_transactions WHERE user_id = ?", (USER2,))

    # 3. User 1 — rewrite portfolio_holdings to match new ISIN mappings, then
    #    seed lots + NAVs + metadata. portfolio_holdings.ticker_symbol now
    #    matches mf_transactions.scheme_code so the joins line up.
    _rewrite_portfolio_for(cur, USER1, USER1_HOLDINGS)
    for h in USER1_HOLDINGS:
        _ensure_metadata(cur, h)
        _ensure_nav(cur, h)
        _seed_lots(cur, h)

    # 4. User 2 — bootstrap portfolio rows, then lots + NAVs + metadata.
    _rewrite_portfolio_for(cur, USER2, USER2_HOLDINGS)
    for h in USER2_HOLDINGS:
        _ensure_metadata(cur, h)
        _ensure_nav(cur, h)
        _seed_lots(cur, h)

    # 5. Tax profiles.
    _seed_tax_profile(cur, USER1, regime="new", rate=30)
    _seed_tax_profile(cur, USER2, regime="new", rate=20)

    # 6. Seed NAV stubs for every other recommended ISIN — the input builder
    #    prices every recommended fund, not just the held ones.
    seeded = _seed_all_csv_fund_navs(cur)
    print(f"seeded NAV stubs for {seeded} additional CSV recommended funds")

    con.commit()

    # 7. Quick reconciliation report.
    print("\n--- reconciliation ---")
    for u, label in ((USER1, "8888888881"), (USER2, "8888888882")):
        cur.execute(
            """
            SELECT t.scheme_code,
                   SUM(t.units),
                   SUM(t.amount),
                   SUM(t.units) * (SELECT n.nav FROM mf_nav_history n
                                    WHERE n.scheme_code = t.scheme_code
                                    ORDER BY n.nav_date DESC LIMIT 1) AS market_value
              FROM mf_transactions t
             WHERE t.user_id = ?
             GROUP BY t.scheme_code
             ORDER BY t.scheme_code
            """, (u,),
        )
        rows = cur.fetchall()
        print(f"\nUSER {label}:")
        print(f"  {'scheme':<12} {'units':>12} {'invested':>14} {'market_val':>14}")
        total_inv = total_mkt = Decimal(0)
        for sc, units, invested, mkt in rows:
            inv_d = Decimal(str(invested))
            mkt_d = Decimal(str(mkt))
            total_inv += inv_d
            total_mkt += mkt_d
            print(f"  {sc:<12} {Decimal(str(units)):>12} {inv_d:>14,.2f} {mkt_d:>14,.2f}")
        print(f"  {'TOTAL':<12} {'':>12} {total_inv:>14,.2f} {total_mkt:>14,.2f}")

    con.close()
    print("\nseed complete.")


if __name__ == "__main__":
    main()
