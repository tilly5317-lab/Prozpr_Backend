from datetime import date
import pytest
from goal_planning.models import Assumptions, ClientProfile, CurrentProperty
from goal_planning.engine.mortgages import build_existing_mortgages
from goal_planning.engine.profile import build_initial_context


def _ctx():
    return build_initial_context(
        ClientProfile(
            latest_update_date=date(2026, 5, 9), annual_income=2_000_000, tax_rate=0.30,
            financial_assets=20_000_000, financial_liabilities_excl_mortgage=5_000_000,
            monthly_household_expense=80_000,
        ),
        Assumptions(),
    )


def test_skips_property_without_mortgage():
    props = [CurrentProperty(name="apt_paid_off", has_mortgage=False)]
    schedules = build_existing_mortgages(props, _ctx(), [])
    assert schedules == []


def test_rate_inversion_round_trip():
    # 50L principal, 240 months, EMI ~43,391 -> infer ~8.5% nominal
    props = [CurrentProperty(
        name="apt_1", has_mortgage=True,
        mortgage_balance=5_000_000, mortgage_emi=43_391,
        mortgage_last_date=date(2046, 5, 9),
    )]
    schedules = build_existing_mortgages(props, _ctx(), [])
    assert len(schedules) == 1
    sched = schedules[0]
    assert sched.property_ref == "existing:apt_1"
    assert len(sched.monthly_rows) > 0
    first = sched.monthly_rows[0]
    assert first.interest_portion > 0
    assert first.principal_portion > 0
    assert first.opening_balance == pytest.approx(5_000_000, rel=1e-3)
    assert first.emi == pytest.approx(43_391, rel=1e-3)


def test_first_fy_proration():
    """First FY EMI total < 12 x EMI when latest_update is mid-FY."""
    props = [CurrentProperty(
        name="apt_1", has_mortgage=True,
        mortgage_balance=5_000_000, mortgage_emi=43_391,
        mortgage_last_date=date(2046, 5, 9),
    )]
    schedules = build_existing_mortgages(props, _ctx(), [])
    sched = schedules[0]
    first_fy_row = sched.annual_rows[0]
    assert first_fy_row.annual_emi_total < 43_391 * 12
    assert first_fy_row.annual_emi_total > 43_391 * 8


def test_skips_already_paid_off_mortgage():
    props = [CurrentProperty(
        name="paid_off_apt", has_mortgage=True,
        mortgage_balance=5_000_000, mortgage_emi=43_391,
        mortgage_last_date=date(2020, 1, 1),
    )]
    warnings: list[str] = []
    schedules = build_existing_mortgages(props, _ctx(), warnings)
    assert schedules == []
    assert any("already" in w.lower() for w in warnings)
