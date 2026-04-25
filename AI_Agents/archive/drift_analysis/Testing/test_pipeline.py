from __future__ import annotations

import sys
from pathlib import Path

_SRC_ROOT = Path(__file__).resolve().parents[3]
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

import pytest

from goal_based_allocation_pydantic import run_allocation
from goal_based_allocation_pydantic.models import (
    AllocationInput,
    Goal,
    GoalAllocationOutput,
)
from goal_based_allocation_pydantic.tables import FUND_MAPPING

from drift_analysis import ActualHolding, DriftInput, compute_drift


# ── Shared profiles (mirrors the 5 profiles in goal_based_allocation_pydantic conftest) ──


@pytest.fixture(scope="module")
def minimal_input() -> AllocationInput:
    return AllocationInput(
        effective_risk_score=5.0,
        age=35,
        annual_income=1_500_000,
        osi=0.4,
        savings_rate_adjustment="none",
        gap_exceeds_3=False,
        total_corpus=1_000_000,
        monthly_household_expense=50_000,
        tax_regime="new",
        section_80c_utilized=0.0,
        emergency_fund_needed=True,
        primary_income_from_portfolio=False,
        effective_tax_rate=15.0,
        goals=[
            Goal(goal_name="Car", time_to_goal_months=18, amount_needed=300_000, goal_priority="negotiable"),
            Goal(goal_name="Retirement", time_to_goal_months=240, amount_needed=400_000, goal_priority="non_negotiable", investment_goal="retirement"),
        ],
        net_financial_assets=500_000,
    )


@pytest.fixture(scope="module")
def high_risk_input() -> AllocationInput:
    return AllocationInput(
        effective_risk_score=9.0,
        age=30,
        annual_income=3_000_000,
        osi=0.5,
        savings_rate_adjustment="equity_boost",
        gap_exceeds_3=False,
        total_corpus=5_000_000,
        monthly_household_expense=100_000,
        tax_regime="new",
        effective_tax_rate=30.0,
        goals=[
            Goal(goal_name="Wealth", time_to_goal_months=300, amount_needed=4_000_000, goal_priority="non_negotiable", investment_goal="wealth_creation"),
        ],
        net_financial_assets=2_000_000,
    )


@pytest.fixture(scope="module")
def low_tax_input() -> AllocationInput:
    return AllocationInput(
        effective_risk_score=5.0,
        age=40,
        annual_income=800_000,
        osi=0.3,
        savings_rate_adjustment="none",
        gap_exceeds_3=False,
        total_corpus=1_500_000,
        monthly_household_expense=40_000,
        tax_regime="new",
        effective_tax_rate=10.0,
        goals=[
            Goal(goal_name="Laptop", time_to_goal_months=12, amount_needed=100_000, goal_priority="negotiable"),
            Goal(goal_name="House downpayment", time_to_goal_months=36, amount_needed=600_000, goal_priority="non_negotiable", investment_goal="home_purchase"),
            Goal(goal_name="Retirement", time_to_goal_months=240, amount_needed=500_000, goal_priority="non_negotiable", investment_goal="retirement"),
        ],
        net_financial_assets=300_000,
    )


@pytest.fixture(scope="module")
def old_regime_input() -> AllocationInput:
    return AllocationInput(
        effective_risk_score=6.0,
        age=45,
        annual_income=2_000_000,
        osi=0.5,
        savings_rate_adjustment="none",
        gap_exceeds_3=False,
        total_corpus=3_000_000,
        monthly_household_expense=80_000,
        tax_regime="old",
        section_80c_utilized=50_000.0,
        effective_tax_rate=25.0,
        goals=[
            Goal(goal_name="Retirement", time_to_goal_months=180, amount_needed=2_000_000, goal_priority="non_negotiable", investment_goal="retirement"),
        ],
        net_financial_assets=1_000_000,
    )


@pytest.fixture(scope="module")
def intergen_input() -> AllocationInput:
    return AllocationInput(
        effective_risk_score=5.0,
        age=65,
        annual_income=1_500_000,
        osi=0.6,
        savings_rate_adjustment="none",
        gap_exceeds_3=False,
        total_corpus=8_000_000,
        monthly_household_expense=100_000,
        tax_regime="new",
        effective_tax_rate=20.0,
        primary_income_from_portfolio=True,
        goals=[
            Goal(
                goal_name="Family legacy",
                time_to_goal_months=180,
                amount_needed=5_000_000,
                goal_priority="non_negotiable",
                investment_goal="intergenerational_transfer",
            ),
        ],
        net_financial_assets=4_000_000,
    )


# ── Module-scoped allocations (run once per test session) ─────────────────────


@pytest.fixture(scope="module")
def minimal_allocation(minimal_input) -> GoalAllocationOutput:
    return run_allocation(minimal_input)


@pytest.fixture(scope="module")
def high_risk_allocation(high_risk_input) -> GoalAllocationOutput:
    return run_allocation(high_risk_input)


@pytest.fixture(scope="module")
def low_tax_allocation(low_tax_input) -> GoalAllocationOutput:
    return run_allocation(low_tax_input)


@pytest.fixture(scope="module")
def old_regime_allocation(old_regime_input) -> GoalAllocationOutput:
    return run_allocation(old_regime_input)


@pytest.fixture(scope="module")
def intergen_allocation(intergen_input) -> GoalAllocationOutput:
    return run_allocation(intergen_input)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _recommended_holdings_from(ideal: GoalAllocationOutput) -> list[ActualHolding]:
    """Build one ActualHolding per subgroup using the recommended fund at the ideal amount."""
    holdings = []
    for row in ideal.aggregated_subgroups:
        if row.total <= 0:
            continue
        fm = FUND_MAPPING.get(row.subgroup)
        if fm is None:
            continue
        holdings.append(ActualHolding(
            scheme_code=fm.asset_subgroup,
            scheme_name=fm.recommended_fund,
            isin=fm.isin,
            asset_class=fm.asset_class,
            asset_subgroup=row.subgroup,
            current_value=row.total,
            invested_amount=row.total,
        ))
    return holdings


def _scale_holdings(holdings: list[ActualHolding], factor: float) -> list[ActualHolding]:
    """Return a copy of holdings with current_value scaled by *factor*."""
    return [
        ActualHolding(
            scheme_code=h.scheme_code,
            scheme_name=h.scheme_name,
            isin=h.isin,
            asset_class=h.asset_class,
            asset_subgroup=h.asset_subgroup,
            current_value=round(h.current_value * factor, 2),
            invested_amount=h.invested_amount,
        )
        for h in holdings
    ]


# ── Test: perfect match (zero drift) for each profile ────────────────────────


class TestPerfectMatch:
    """All five profiles: holding exactly the recommended funds at ideal amounts → zero drift."""

    def _assert_zero_drift(self, ideal: GoalAllocationOutput) -> None:
        holdings = _recommended_holdings_from(ideal)
        out = compute_drift(DriftInput(ideal_allocation=ideal, actual_holdings=holdings))

        assert out.total_ideal_value == pytest.approx(ideal.grand_total, abs=1)
        actual_sum = sum(h.current_value for h in holdings)
        assert out.total_actual_value == pytest.approx(actual_sum, abs=1)

        for ac in out.asset_classes:
            assert ac.drift_amount == pytest.approx(0.0, abs=1), \
                f"Expected zero drift for {ac.asset_class}, got {ac.drift_amount}"
            for sg in ac.subgroups:
                assert sg.drift_amount == pytest.approx(0.0, abs=1), \
                    f"Expected zero drift for subgroup {sg.subgroup}, got {sg.drift_amount}"

    def test_minimal_profile(self, minimal_allocation):
        self._assert_zero_drift(minimal_allocation)

    def test_high_risk_profile(self, high_risk_allocation):
        self._assert_zero_drift(high_risk_allocation)

    def test_low_tax_profile(self, low_tax_allocation):
        self._assert_zero_drift(low_tax_allocation)

    def test_old_regime_profile(self, old_regime_allocation):
        self._assert_zero_drift(old_regime_allocation)

    def test_intergen_profile(self, intergen_allocation):
        self._assert_zero_drift(intergen_allocation)


# ── Test: overweight (holdings > ideal) ──────────────────────────────────────


class TestOverweight:
    """Holdings are 20% above ideal — every asset class should show positive drift."""

    def _assert_overweight(self, ideal: GoalAllocationOutput) -> None:
        base_holdings = _recommended_holdings_from(ideal)
        overweight_holdings = _scale_holdings(base_holdings, 1.2)

        out = compute_drift(DriftInput(ideal_allocation=ideal, actual_holdings=overweight_holdings))

        total_expected_drift = pytest.approx(ideal.grand_total * 0.2, rel=0.02)
        actual_drift = out.total_actual_value - out.total_ideal_value
        assert actual_drift == total_expected_drift

        for ac in out.asset_classes:
            assert ac.drift_amount > 0, \
                f"{ac.asset_class} should be overweight but drift_amount={ac.drift_amount}"

    def test_minimal_overweight(self, minimal_allocation):
        self._assert_overweight(minimal_allocation)

    def test_high_risk_overweight(self, high_risk_allocation):
        self._assert_overweight(high_risk_allocation)

    def test_low_tax_overweight(self, low_tax_allocation):
        self._assert_overweight(low_tax_allocation)

    def test_old_regime_overweight(self, old_regime_allocation):
        self._assert_overweight(old_regime_allocation)

    def test_intergen_overweight(self, intergen_allocation):
        self._assert_overweight(intergen_allocation)


# ── Test: underweight (holdings < ideal) ─────────────────────────────────────


class TestUnderweight:
    """Holdings are 30% below ideal — every asset class should show negative drift."""

    def _assert_underweight(self, ideal: GoalAllocationOutput) -> None:
        base_holdings = _recommended_holdings_from(ideal)
        underweight_holdings = _scale_holdings(base_holdings, 0.7)

        out = compute_drift(DriftInput(ideal_allocation=ideal, actual_holdings=underweight_holdings))

        for ac in out.asset_classes:
            assert ac.drift_amount < 0, \
                f"{ac.asset_class} should be underweight but drift_amount={ac.drift_amount}"

    def test_minimal_underweight(self, minimal_allocation):
        self._assert_underweight(minimal_allocation)

    def test_high_risk_underweight(self, high_risk_allocation):
        self._assert_underweight(high_risk_allocation)

    def test_low_tax_underweight(self, low_tax_allocation):
        self._assert_underweight(low_tax_allocation)

    def test_old_regime_underweight(self, old_regime_allocation):
        self._assert_underweight(old_regime_allocation)

    def test_intergen_underweight(self, intergen_allocation):
        self._assert_underweight(intergen_allocation)


# ── Test: empty holdings — all allocations underweight ───────────────────────


class TestEmptyHoldings:
    """No holdings at all → every subgroup should be negative drift."""

    def _assert_all_underweight(self, ideal: GoalAllocationOutput) -> None:
        out = compute_drift(DriftInput(ideal_allocation=ideal, actual_holdings=[]))

        assert out.total_actual_value == 0.0
        assert out.total_ideal_value == pytest.approx(ideal.grand_total, abs=1)

        for ac in out.asset_classes:
            assert ac.drift_amount < 0
            for sg in ac.subgroups:
                assert sg.drift_amount < 0
                assert len(sg.funds) == 1
                assert sg.funds[0].is_recommended is True
                assert sg.funds[0].actual_amount == 0.0

    def test_minimal_empty(self, minimal_allocation):
        self._assert_all_underweight(minimal_allocation)

    def test_high_risk_empty(self, high_risk_allocation):
        self._assert_all_underweight(high_risk_allocation)

    def test_low_tax_empty(self, low_tax_allocation):
        self._assert_all_underweight(low_tax_allocation)

    def test_old_regime_empty(self, old_regime_allocation):
        self._assert_all_underweight(old_regime_allocation)

    def test_intergen_empty(self, intergen_allocation):
        self._assert_all_underweight(intergen_allocation)


# ── Test: non-recommended funds (different ISIN, same subgroup) ───────────────


class TestNonRecommendedFunds:
    """Customer holds a different fund in the same subgroup — recommended fund shows up with ideal amount, non-recommended shows zero ideal."""

    def test_non_recommended_shows_ideal_zero(self, minimal_allocation):
        ideal = minimal_allocation
        # Pick the first subgroup with a positive allocation
        target_row = next(r for r in ideal.aggregated_subgroups if r.total > 0)
        subgroup = target_row.subgroup
        fm = FUND_MAPPING[subgroup]

        # Hold a different fund in that subgroup
        different_holding = ActualHolding(
            scheme_code="NONREC01",
            scheme_name="Some Other Fund",
            isin="INF000NONREC01",
            asset_class=fm.asset_class,
            asset_subgroup=subgroup,
            current_value=target_row.total,
            invested_amount=target_row.total,
        )
        out = compute_drift(DriftInput(ideal_allocation=ideal, actual_holdings=[different_holding]))

        # Find the subgroup in drift output
        sg_drift = None
        for ac in out.asset_classes:
            for sg in ac.subgroups:
                if sg.subgroup == subgroup:
                    sg_drift = sg
                    break

        assert sg_drift is not None, f"subgroup {subgroup} not found in output"
        assert sg_drift.drift_amount == pytest.approx(0.0, abs=1)  # subgroup total is the same

        rec = next(f for f in sg_drift.funds if f.is_recommended)
        non_rec = next(f for f in sg_drift.funds if not f.is_recommended)

        assert rec.actual_amount == 0.0
        assert rec.ideal_amount == pytest.approx(target_row.total, abs=1)
        assert rec.drift_amount == pytest.approx(-target_row.total, abs=1)

        assert non_rec.actual_amount == pytest.approx(target_row.total, abs=1)
        assert non_rec.ideal_amount == 0.0
        assert non_rec.drift_amount == pytest.approx(target_row.total, abs=1)


# ── Test: multi-folio aggregation ─────────────────────────────────────────────


class TestMultiFolioAggregation:
    """Same ISIN in two folios is merged into one FundDrift entry."""

    def test_two_folios_same_fund(self, minimal_allocation):
        ideal = minimal_allocation
        target_row = next(r for r in ideal.aggregated_subgroups if r.total > 0)
        subgroup = target_row.subgroup
        fm = FUND_MAPPING[subgroup]
        half = target_row.total / 2.0

        holdings = [
            ActualHolding(
                scheme_code=fm.asset_subgroup,
                scheme_name=fm.recommended_fund,
                isin=fm.isin,
                asset_class=fm.asset_class,
                asset_subgroup=subgroup,
                current_value=half,
                invested_amount=half,
            ),
            ActualHolding(
                scheme_code=fm.asset_subgroup,
                scheme_name=fm.recommended_fund,
                isin=fm.isin,
                asset_class=fm.asset_class,
                asset_subgroup=subgroup,
                current_value=half,
                invested_amount=half,
            ),
        ]
        out = compute_drift(DriftInput(ideal_allocation=ideal, actual_holdings=holdings))

        sg_drift = next(
            sg for ac in out.asset_classes for sg in ac.subgroups if sg.subgroup == subgroup
        )
        # Even though it came from two holdings, there should be just one fund entry
        assert len(sg_drift.funds) == 1
        assert sg_drift.funds[0].actual_amount == pytest.approx(target_row.total, abs=1)


# ── Test: unmapped subgroup goes to "others" ──────────────────────────────────


class TestUnmappedSubgroup:
    """A holding with an unknown subgroup is classified under 'others'."""

    def test_unknown_subgroup_in_others(self, minimal_allocation):
        ideal = minimal_allocation
        holdings = _recommended_holdings_from(ideal) + [
            ActualHolding(
                scheme_code="EXOTIC01",
                scheme_name="Exotic Fund",
                isin="INF000EXOTIC01",
                asset_class="others",
                asset_subgroup="completely_unknown_subgroup",
                current_value=50_000,
                invested_amount=50_000,
            ),
        ]
        out = compute_drift(DriftInput(ideal_allocation=ideal, actual_holdings=holdings))

        others_ac = next((ac for ac in out.asset_classes if ac.asset_class == "others"), None)
        assert others_ac is not None
        # The exotic fund's value must appear somewhere inside "others"
        assert any(
            sg.actual_amount >= 50_000
            for sg in others_ac.subgroups
        )


# ── Test: drift_pct uses total_ideal_value as denominator ─────────────────────


class TestDriftPctCalculation:
    """drift_pct at asset-class level = drift_amount / total_ideal * 100."""

    def test_drift_pct_formula(self, old_regime_allocation):
        ideal = old_regime_allocation
        base = _recommended_holdings_from(ideal)
        # Scale each holding: equity +25%, debt −25%
        scaled = []
        for h in base:
            if h.asset_class == "equity":
                scaled.append(_scale_holdings([h], 1.25)[0])
            elif h.asset_class == "debt":
                scaled.append(_scale_holdings([h], 0.75)[0])
            else:
                scaled.append(h)

        out = compute_drift(DriftInput(ideal_allocation=ideal, actual_holdings=scaled))

        for ac in out.asset_classes:
            if out.total_ideal_value > 0:
                expected_pct = round(ac.drift_amount / out.total_ideal_value * 100, 2)
                assert ac.drift_pct == pytest.approx(expected_pct, abs=0.01), \
                    f"{ac.asset_class}: expected drift_pct={expected_pct}, got {ac.drift_pct}"


# ── Test: display_name resolution ─────────────────────────────────────────────


class TestDisplayNames:
    """display_name on SubgroupDrift matches the FUND_MAPPING sub_category (or override)."""

    def test_display_names_from_fund_mapping(self, high_risk_allocation):
        ideal = high_risk_allocation
        out = compute_drift(DriftInput(ideal_allocation=ideal, actual_holdings=[]))

        for ac in out.asset_classes:
            for sg in ac.subgroups:
                fm = FUND_MAPPING.get(sg.subgroup)
                if fm:
                    # Either direct sub_category or an override like "Arbitrage Income"
                    assert sg.display_name, f"display_name is empty for {sg.subgroup}"
                    assert isinstance(sg.display_name, str)
                    assert len(sg.display_name) > 0

    def test_arbitrage_income_override(self, low_tax_allocation):
        """arbitrage_plus_income subgroup should have display_name 'Arbitrage Income'."""
        ideal = low_tax_allocation
        # Check only if this subgroup is present in the allocation
        has_arb = any(r.subgroup == "arbitrage_plus_income" and r.total > 0
                      for r in ideal.aggregated_subgroups)
        if not has_arb:
            pytest.skip("arbitrage_plus_income not allocated in low_tax profile")

        out = compute_drift(DriftInput(ideal_allocation=ideal, actual_holdings=[]))

        for ac in out.asset_classes:
            for sg in ac.subgroups:
                if sg.subgroup == "arbitrage_plus_income":
                    assert sg.display_name == "Arbitrage Income"


# ── Test: fund drift sums equal subgroup drift equal asset-class drift ─────────


class TestAggregationInvariants:
    """sum(fund.drift_amount) == subgroup.drift_amount and sum(sg.drift) == ac.drift."""

    def _assert_aggregation(self, ideal: GoalAllocationOutput) -> None:
        holdings = _recommended_holdings_from(ideal)
        # Add a non-recommended fund to create fund-level splits
        if holdings:
            first = holdings[0]
            extra = ActualHolding(
                scheme_code="EXTRA01",
                scheme_name="Extra Non-Rec Fund",
                isin="INF000EXTRA01",
                asset_class=first.asset_class,
                asset_subgroup=first.asset_subgroup,
                current_value=10_000,
                invested_amount=10_000,
            )
            holdings = holdings + [extra]

        out = compute_drift(DriftInput(ideal_allocation=ideal, actual_holdings=holdings))

        for ac in out.asset_classes:
            sg_drift_sum = sum(sg.drift_amount for sg in ac.subgroups)
            assert sg_drift_sum == pytest.approx(ac.drift_amount, abs=1), \
                f"{ac.asset_class}: sum(subgroup drifts)={sg_drift_sum} != ac.drift={ac.drift_amount}"

            for sg in ac.subgroups:
                fund_drift_sum = sum(f.drift_amount for f in sg.funds)
                assert fund_drift_sum == pytest.approx(sg.drift_amount, abs=1), \
                    f"{sg.subgroup}: sum(fund drifts)={fund_drift_sum} != sg.drift={sg.drift_amount}"

    def test_minimal_aggregation(self, minimal_allocation):
        self._assert_aggregation(minimal_allocation)

    def test_high_risk_aggregation(self, high_risk_allocation):
        self._assert_aggregation(high_risk_allocation)

    def test_low_tax_aggregation(self, low_tax_allocation):
        self._assert_aggregation(low_tax_allocation)

    def test_old_regime_aggregation(self, old_regime_allocation):
        self._assert_aggregation(old_regime_allocation)

    def test_intergen_aggregation(self, intergen_allocation):
        self._assert_aggregation(intergen_allocation)


# ── Test: DriftOutput structure completeness ──────────────────────────────────


class TestOutputStructure:
    """Verify the output has all required fields populated for each profile."""

    def _assert_structure(self, ideal: GoalAllocationOutput) -> None:
        holdings = _recommended_holdings_from(ideal)
        out = compute_drift(DriftInput(ideal_allocation=ideal, actual_holdings=holdings))

        assert out.total_ideal_value >= 0
        assert out.total_actual_value >= 0
        assert isinstance(out.asset_classes, list)

        for ac in out.asset_classes:
            assert ac.asset_class in ("equity", "debt", "others")
            assert isinstance(ac.subgroups, list)
            assert len(ac.subgroups) > 0

            for sg in ac.subgroups:
                assert isinstance(sg.display_name, str) and sg.display_name
                assert isinstance(sg.funds, list)
                assert len(sg.funds) > 0

                for f in sg.funds:
                    assert isinstance(f.is_recommended, bool)
                    assert f.ideal_amount >= 0
                    assert f.actual_amount >= 0

    def test_minimal_structure(self, minimal_allocation):
        self._assert_structure(minimal_allocation)

    def test_high_risk_structure(self, high_risk_allocation):
        self._assert_structure(high_risk_allocation)

    def test_low_tax_structure(self, low_tax_allocation):
        self._assert_structure(low_tax_allocation)

    def test_old_regime_structure(self, old_regime_allocation):
        self._assert_structure(old_regime_allocation)

    def test_intergen_structure(self, intergen_allocation):
        self._assert_structure(intergen_allocation)
