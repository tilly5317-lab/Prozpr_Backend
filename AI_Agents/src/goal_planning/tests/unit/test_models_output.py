from datetime import date, datetime
from goal_planning.models import (
    HeadlineStatus, RetirementSnapshot, GoalFundingStatus, OneOffFundingStatus,
    AnnualCashflowRow, MonthlyCashflowRow, MonthlyNFARow,
    MortgageAmortizationRow, MortgageAmortization,
    FundFlowSummary, ValidationIssue, GoalType,
)


def test_headline_status_construction():
    h = HeadlineStatus(
        horizon_years=20, last_goal_date=date(2046, 1, 1), last_fy_end_date=date(2046, 3, 31),
        number_of_goals=3, net_financial_assets_today=15_000_000, sum_fund_today_pv=10_000_000,
        present_status=5_000_000, closing_nfa=3_000_000, total_shortfall_fv=0,
        total_funded_amount=12_000_000, is_overall_feasible=True,
        overall_shortfall_pv=0, overall_shortfall_fv=0,
    )
    assert h.is_overall_feasible


def test_retirement_snapshot_used_picks_override_when_set():
    s = RetirementSnapshot(
        retirement_date=date(2036, 5, 9), years_to_retirement=10.0,
        annual_household_expense_at_retirement=1_500_000, post_retirement_years=25,
        real_roi_annual=0.0283, real_roi_monthly=0.0023,
        corpus_required_computed=30_000_000, corpus_required_user_override=40_000_000,
        corpus_required_used=40_000_000,
    )
    assert s.corpus_required_used == s.corpus_required_user_override


def test_goal_funding_status_positive_shortfall_convention():
    g = GoalFundingStatus(
        name="college", goal_type=GoalType.child_local_education, goal_date=date(2035, 1, 1),
        amount_pv=1_000_000, amount_fv=2_000_000, fund_today_pv=1_500_000,
        funded_amount=1_400_000, is_funded=False, shortfall_fv=600_000, shortfall_pv=400_000,
        expected_roi=0.07,
    )
    assert g.shortfall_fv > 0
    assert g.funded_amount + g.shortfall_fv == g.amount_fv


def test_monthly_nfa_row_kind_literals():
    r = MonthlyNFARow(
        month_end=date(2026, 5, 31), fy_label="FY27", nfa_open=10_000_000, regular_invest=50_000,
        regular_invest_kind="user_sip", roi=80_000, one_off_in=0, goal_outflow_total=0,
        nfa_close=10_130_000, savings_2_avg=70_000, funded_flag=True,
    )
    assert r.regular_invest_kind == "user_sip"


def test_validation_issue_severity():
    v = ValidationIssue(field="retirement.date_of_birth", message="missing", severity="error")
    assert v.severity == "error"
