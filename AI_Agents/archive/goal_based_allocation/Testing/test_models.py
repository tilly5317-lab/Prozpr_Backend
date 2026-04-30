"""Unit tests for goal_based_allocation Pydantic models."""
import pytest
from pydantic import ValidationError
from src.goal_based_allocation.models import (
    Goal, AllocationInput, BucketShortfall, BucketAllocation,
    AggregatedSubgroupRow, ClientSummary,
    GoalAllocationOutput,
)


# ── Goal ──────────────────────────────────────────────────────────────────────

def test_goal_valid():
    g = Goal(goal_name="Retirement", time_to_goal_months=240,
              amount_needed=5_000_000, goal_priority="non_negotiable")
    assert g.goal_name == "Retirement"
    assert g.time_to_goal_months == 240


def test_goal_priority_values():
    Goal(goal_name="Holiday", time_to_goal_months=18,
         amount_needed=100_000, goal_priority="negotiable")
    Goal(goal_name="Education", time_to_goal_months=36,
         amount_needed=500_000, goal_priority="non_negotiable")


def test_goal_invalid_priority():
    with pytest.raises(ValidationError):
        Goal(goal_name="X", time_to_goal_months=12,
             amount_needed=100, goal_priority="maybe")


def test_goal_negative_amount_invalid():
    with pytest.raises(ValidationError):
        Goal(goal_name="X", time_to_goal_months=12,
             amount_needed=-1, goal_priority="negotiable")


def test_goal_time_to_goal_zero_invalid():
    with pytest.raises(ValidationError):
        Goal(goal_name="X", time_to_goal_months=0,
             amount_needed=100, goal_priority="negotiable")


# ── AllocationInput ───────────────────────────────────────────────────────────

def _base_input(**overrides) -> dict:
    base = dict(
        effective_risk_score=7.0,
        age=35,
        annual_income=2_000_000,
        osi=0.8,
        savings_rate_adjustment="equity_boost",
        gap_exceeds_3=False,
        total_corpus=3_000_000,
        monthly_household_expense=60_000,
        tax_regime="old",
        section_80c_utilized=0.0,
        effective_tax_rate=30.0,
        goals=[],
    )
    base.update(overrides)
    return base


def test_allocation_input_valid():
    inp = AllocationInput(**_base_input())
    assert inp.effective_risk_score == 7.0
    assert inp.effective_tax_rate == 30.0
    assert inp.goals == []


def test_allocation_input_with_goals():
    goals = [
        Goal(goal_name="Car", time_to_goal_months=18,
             amount_needed=800_000, goal_priority="negotiable"),
        Goal(goal_name="Retirement", time_to_goal_months=300,
             amount_needed=10_000_000, goal_priority="non_negotiable"),
    ]
    inp = AllocationInput(**_base_input(goals=goals))
    assert len(inp.goals) == 2


def test_allocation_input_tax_rate_out_of_range():
    with pytest.raises(ValidationError):
        AllocationInput(**_base_input(effective_tax_rate=110.0))


def test_allocation_input_risk_score_out_of_range():
    with pytest.raises(ValidationError):
        AllocationInput(**_base_input(effective_risk_score=11.0))


def test_allocation_input_no_investment_horizon_field():
    """Confirm removed fields do not exist on the model."""
    inp = AllocationInput(**_base_input())
    assert not hasattr(inp, "investment_horizon")
    assert not hasattr(inp, "investment_horizon_years")
    assert not hasattr(inp, "investment_goal")
    assert not hasattr(inp, "short_term_expenses")


def test_allocation_input_risk_score_lower_bound():
    with pytest.raises(ValidationError):
        AllocationInput(**_base_input(effective_risk_score=0.5))


def test_allocation_input_tax_rate_lower_bound():
    with pytest.raises(ValidationError):
        AllocationInput(**_base_input(effective_tax_rate=-5.0))


def test_allocation_input_negative_corpus_invalid():
    with pytest.raises(ValidationError):
        AllocationInput(**_base_input(total_corpus=-100))


# ── Output models ─────────────────────────────────────────────────────────────

def test_bucket_shortfall_valid():
    s = BucketShortfall(
        bucket="short_term",
        shortfall_amount=100_000,
        message="Insufficient corpus for short-term goals.",
    )
    assert s.bucket == "short_term"


def test_goal_allocation_output_valid():
    goals = [Goal(goal_name="R", time_to_goal_months=300,
                  amount_needed=1_000_000, goal_priority="non_negotiable")]
    summary = ClientSummary(age=35, effective_risk_score=7.0,
                            total_corpus=3_000_000, goals=goals)
    out = GoalAllocationOutput(
        client_summary=summary,
        bucket_allocations=[],
        aggregated_subgroups=[],
        shortfall_summary=[],
        grand_total=3_000_000,
        all_amounts_in_multiples_of_100=True,
    )
    assert out.grand_total == 3_000_000


# Constraint lower bounds
def test_bucket_shortfall_invalid_bucket():
    with pytest.raises(ValidationError):
        BucketShortfall(bucket="ultra_short", shortfall_amount=100, message="x")


def test_bucket_allocation_valid():
    goals = [Goal(goal_name="G", time_to_goal_months=12,
                  amount_needed=100_000, goal_priority="negotiable")]
    ba = BucketAllocation(
        bucket="short_term",
        goals=goals,
        total_goal_amount=100_000,
        allocated_amount=100_000,
        shortfall=None,
        subgroup_amounts={"debt_subgroup": 100_000},
    )
    assert ba.bucket == "short_term"
    assert ba.shortfall is None


def test_aggregated_subgroup_row_valid():
    row = AggregatedSubgroupRow(
        subgroup="debt_subgroup",
        sub_category="Liquid Fund",
        emergency=180_000,
        short_term=0,
        medium_term=0,
        long_term=0,
        total=180_000,
        fund_mapping=None,
    )
    assert row.total == 180_000


# ── MarketCommentaryScores ────────────────────────────────────────────────────

def test_market_commentary_scores_defaults():
    from src.goal_based_allocation.models import MarketCommentaryScores
    scores = MarketCommentaryScores()
    assert scores.equities == 5.0
    assert scores.debt == 5.0
    assert scores.others == 5.0
    assert scores.low_beta_equities == 5.0
    assert scores.value_equities == 5.0
    assert scores.dividend_equities == 5.0
    assert scores.medium_beta_equities == 5.0
    assert scores.high_beta_equities == 5.0
    assert scores.sector_equities == 5.0
    assert scores.us_equities == 5.0


def test_market_commentary_scores_custom():
    from src.goal_based_allocation.models import MarketCommentaryScores
    scores = MarketCommentaryScores(equities=8.0, debt=3.0)
    assert scores.equities == 8.0
    assert scores.debt == 3.0
    assert scores.others == 5.0  # default unchanged


def test_market_commentary_scores_out_of_range():
    from src.goal_based_allocation.models import MarketCommentaryScores
    with pytest.raises(ValidationError):
        MarketCommentaryScores(equities=11.0)

    with pytest.raises(ValidationError):
        MarketCommentaryScores(debt=0.0)


# ── Goal.investment_goal ──────────────────────────────────────────────────────

def test_goal_investment_goal_default():
    g = Goal(goal_name="Retirement", time_to_goal_months=240,
             amount_needed=5_000_000, goal_priority="non_negotiable")
    assert g.investment_goal == "wealth_creation"


def test_goal_investment_goal_intergenerational():
    g = Goal(goal_name="Estate Transfer", time_to_goal_months=120,
             amount_needed=5_000_000, goal_priority="non_negotiable",
             investment_goal="intergenerational_transfer")
    assert g.investment_goal == "intergenerational_transfer"


def test_goal_investment_goal_invalid():
    with pytest.raises(ValidationError):
        Goal(goal_name="X", time_to_goal_months=120,
             amount_needed=100, goal_priority="negotiable",
             investment_goal="gambling")


def test_goal_all_investment_goal_values():
    for value in ["wealth_creation", "retirement", "intergenerational_transfer",
                  "education", "home_purchase", "other"]:
        g = Goal(goal_name="G", time_to_goal_months=120,
                 amount_needed=100, goal_priority="negotiable",
                 investment_goal=value)
        assert g.investment_goal == value


# ── AllocationInput.market_commentary ────────────────────────────────────────

def test_allocation_input_market_commentary_default():
    from src.goal_based_allocation.models import MarketCommentaryScores
    inp = AllocationInput(**_base_input())
    assert isinstance(inp.market_commentary, MarketCommentaryScores)
    assert inp.market_commentary.equities == 5.0
    assert inp.market_commentary.debt == 5.0


def test_allocation_input_market_commentary_custom():
    inp = AllocationInput(**_base_input(market_commentary={"equities": 7.0, "debt": 4.0, "high_beta_equities": 8.0}))
    assert inp.market_commentary.equities == 7.0
    assert inp.market_commentary.debt == 4.0
    assert inp.market_commentary.others == 5.0  # default
