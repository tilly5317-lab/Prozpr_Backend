from datetime import date
import pytest
from goal_planning.models import Assumptions, ClientProfile
from goal_planning.engine.profile import build_initial_context


def _profile():
    return ClientProfile(
        latest_update_date=date(2026, 5, 9), annual_income=2_000_000, tax_rate=0.30,
        financial_assets=20_000_000, financial_liabilities_excl_mortgage=5_000_000,
        monthly_household_expense=80_000, monthly_investment_next_12m=50_000,
    )


def test_nfa_computation():
    ctx = build_initial_context(_profile(), Assumptions())
    assert ctx.nfa == 15_000_000


def test_annual_household_expense():
    ctx = build_initial_context(_profile(), Assumptions())
    assert ctx.annual_household_expense == 80_000 * 12


def test_current_fy_year_and_end():
    # latest_update 2026-05-09 → current FY is 2027 (closes 2027-03-31)
    ctx = build_initial_context(_profile(), Assumptions())
    assert ctx.current_fy_year == 2027
    assert ctx.current_fy_end == date(2027, 3, 31)


def test_near_term_and_medium_term_anchors():
    ctx = build_initial_context(_profile(), Assumptions())
    assert ctx.near_term_end == date(2029, 3, 31)
    assert ctx.medium_term_end == date(2032, 3, 31)


def test_assumption_snapshot_copied_into_context():
    ctx = build_initial_context(_profile(), Assumptions(roi_long_term_post_tax=0.10))
    assert ctx.long_term_roi == 0.10
    assert ctx.sip_share == 0.75


def test_retirement_fields_unset_initially():
    ctx = build_initial_context(_profile(), Assumptions())
    assert ctx.retirement_date_considered is None
