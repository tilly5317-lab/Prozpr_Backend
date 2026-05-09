from datetime import date
import pytest
from goal_planning.models import RetirementInput, Assumptions, ClientProfile
from goal_planning.engine.retirement import compute_retirement_snapshot
from goal_planning.engine.profile import build_initial_context
from goal_planning.engine.exceptions import MissingDOBError


def _ctx(latest_update=date(2026, 5, 9)):
    return build_initial_context(
        ClientProfile(
            latest_update_date=latest_update, annual_income=2_000_000, tax_rate=0.30,
            financial_assets=20_000_000, financial_liabilities_excl_mortgage=5_000_000,
            monthly_household_expense=80_000,
        ),
        Assumptions(),
    )


def test_corpus_computed_matches_pv_formula():
    inp = RetirementInput(date_of_birth=date(1976, 5, 9))
    snap = compute_retirement_snapshot(inp, _ctx(), [])
    assert snap.years_to_retirement == pytest.approx(10.0, abs=0.1)
    assert snap.post_retirement_years == 25
    assert snap.corpus_required_used == snap.corpus_required_computed
    assert snap.corpus_required_user_override is None


def test_user_override_takes_precedence():
    inp = RetirementInput(
        date_of_birth=date(1976, 5, 9),
        retirement_corpus_pv_override=40_000_000,
    )
    snap = compute_retirement_snapshot(inp, _ctx(), [])
    assert snap.corpus_required_user_override is not None
    assert snap.corpus_required_used != snap.corpus_required_computed
    expected_used_fv = 40_000_000 * (1.06 ** 10)
    assert snap.corpus_required_used == pytest.approx(expected_used_fv, rel=1e-2)


def test_already_retired_branch():
    inp = RetirementInput(date_of_birth=date(1956, 1, 1))
    warnings: list[str] = []
    snap = compute_retirement_snapshot(inp, _ctx(), warnings)
    assert snap.years_to_retirement <= 0
    assert any("already retired" in w.lower() for w in warnings)
