"""Annual rows must carry raw amounts, not just formatted strings.

Passing only ``format_inr_indian`` output made "when will my portfolio reach ₹1
crore?" a comparison of "₹98.5 lakh" against "₹1.02 crore" over 30 rows. The
answer named FY2041 — the last row — and said the corpus there was ₹4.7 crore,
contradicting itself in the same sentence.
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from app.domains.cashflow.services.goal_planning_engine.service import (
    _build_facts_pack,
)

_QUOTED_ONLY = (
    "income",
    "income_tax",
    "household_expense",
    "savings_pre_emi",
    "savings_post_emi",
    "monthly_investment",
    "investment_returns",
    "goal_payout",
)


def _row(fy: str, corpus: int):
    return SimpleNamespace(
        fy_label=fy,
        is_funded=True,
        income=3_600_000,
        income_tax=900_000,
        household_expense=2_400_000,
        savings_pre_emi=1_200_000,
        savings_post_emi=1_000_000,
        monthly_investment=80_000,
        investment_returns=500_000,
        goal_payout=0,
        corpus_closing=corpus,
    )


def _output(rows):
    money = SimpleNamespace
    return SimpleNamespace(
        headline=money(
            years_to_last_goal=26, last_goal_date=date(2052, 7, 1), number_of_goals=2,
            corpus_today=48_300_000, total_corpus_required_today=50_300_000,
            surplus_or_shortfall_today=-2_000_000, corpus_closing=423_700_000,
            is_feasible=True, total_shortfall_fv=0, total_funded_amount=458_300_000,
        ),
        retirement=money(
            retirement_date=date(2047, 7, 8), years_to_retirement=20.96,
            corpus_required_used=163_749_000, corpus_required_pv_today=48_090_819,
            annual_household_expense_today=2_400_000, post_retirement_years=30,
        ),
        fund_flow_summary=money(
            corpus_opening=48_300_000, total_investments=197_200_000,
            total_roi=636_500_000, total_one_off_in=0, total_one_off_out=0,
            total_goals_paid=458_300_000, corpus_closing=423_700_000,
        ),
        goals=[],
        annual_cashflow=rows,
    )


def _user(equity_shares=0, financial_assets=0):
    return SimpleNamespace(
        first_name="X",
        date_of_birth=date(1992, 7, 8),
        personal_finance_profile=SimpleNamespace(
            equity_shares=equity_shares, financial_assets=financial_assets
        ),
    )


def _pack(rows, *, portfolio_value=None, user=None):
    return _build_facts_pack(
        _output(rows),
        user or _user(),
        retirement_age=55,
        retirement_modelled=False,
        portfolio_value=portfolio_value,
    )


def _facts(rows):
    return _pack(rows)["annual_cashflow"]


def test_corpus_closing_is_the_only_raw_amount():
    """It is the only figure compared across years; the rest are quoted verbatim.

    Shipping raw values for all nine cost ~1,400 tokens a turn and bought nothing.
    """
    row = _facts([_row("FY2030", 9_850_000)])[0]

    assert isinstance(row["corpus_closing"], (int, float))
    for field in _QUOTED_ONLY:
        assert isinstance(row[f"{field}_indian"], str), f"{field}_indian missing"
        assert field not in row, f"{field} raw value is dead weight — quoted only"


def test_the_indian_sibling_is_the_formatted_string():
    row = _facts([_row("FY2030", 9_850_000)])[0]

    assert row["corpus_closing"] == 9_850_000
    assert row["corpus_closing_indian"] == "₹98.5 lakh"


def test_a_crore_threshold_is_now_a_numeric_comparison():
    """The ₹1cr bug: 98.5 lakh vs 1.02 crore is only obvious on the raw numbers."""
    rows = _facts([
        _row("FY2029", 8_100_000),
        _row("FY2030", 9_850_000),
        _row("FY2031", 10_200_000),
        _row("FY2040", 40_400_000),
        _row("FY2041", 47_000_000),
    ])

    crossing = next(r for r in rows if r["corpus_closing"] >= 10_000_000)

    assert crossing["fy_label"] == "FY2031", "must be the FIRST crossing, not the last row"


def test_non_money_fields_are_untouched():
    row = _facts([_row("FY2030", 9_850_000)])[0]

    assert row["fy_label"] == "FY2030"
    assert row["is_funded"] is True


def test_corpus_composition_splits_the_merged_total():
    """corpus_today merges three sources; without the split the formatter calls the
    whole thing "your portfolio" and overstates it by the direct-equity holding."""
    pack = _pack(
        [],
        portfolio_value=29_813_090,
        user=_user(equity_shares=20_000_000, financial_assets=0),
    )

    split = pack["corpus_composition"]
    assert split["linked_portfolio_value"] == 29_813_090
    assert split["direct_equity_shares"] == 20_000_000
    assert split["cash_and_debt"] == 0
    assert split["linked_portfolio_value_indian"] == "₹2.98 crore"


def test_corpus_composition_survives_a_missing_profile_or_portfolio():
    split = _pack([], portfolio_value=None, user=SimpleNamespace(
        first_name="X", date_of_birth=date(1992, 7, 8), personal_finance_profile=None
    ))["corpus_composition"]

    assert split["linked_portfolio_value"] == 0
    assert split["direct_equity_shares"] == 0
