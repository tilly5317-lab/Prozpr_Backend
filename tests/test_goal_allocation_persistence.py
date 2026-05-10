from __future__ import annotations

import uuid

from app.models.goals.financial_goal import FinancialGoal
from app.models.goals.goal_allocation import GoalAllocationRecommendation
from app.services.allocation_recommendation_persist import _allocation_output_to_jsonable
from goal_based_allocation_pydantic.models import (
    AggregatedSubgroupRow,
    AssetClassBreakdown,
    AssetClassSplitBlock,
    BucketAllocation,
    BucketAssetClassSplit,
    ClientSummary,
    FutureInvestment,
    Goal,
    GoalAllocationOutput,
    SubgroupBreakdown,
    SubgroupBucketAllocation,
    SubgroupBucketSplit,
    SubgroupFundMapping,
)


def _build_output() -> GoalAllocationOutput:
    goal = Goal(
        goal_name="Retirement",
        time_to_goal_months=240,
        amount_needed=1000000,
        goal_priority="non_negotiable",
        investment_goal="wealth_creation",
    )
    bucket = BucketAllocation(
        bucket="long_term",
        goals=[goal],
        total_goal_amount=1000000,
        allocated_amount=800000,
        future_investment=FutureInvestment(
            bucket="long_term",
            future_investment_amount=200000,
            message="Increase SIP",
        ),
        subgroup_amounts={"low_beta_equities": 500000, "debt_subgroup": 300000},
        rationale="Long horizon allows equity tilt.",
        goal_rationales={"Retirement": "Growth-first allocation for long horizon."},
    )
    fund_map = SubgroupFundMapping(
        asset_class="equity",
        asset_subgroup="low_beta_equities",
        sub_category="Large Cap",
        recommended_fund="Sample Fund",
        isin="INF000000001",
        amount=500000,
    )
    subgroup = AggregatedSubgroupRow(
        subgroup="low_beta_equities",
        sub_category="Large Cap",
        emergency=0,
        short_term=0,
        medium_term=0,
        long_term=500000,
        total=500000,
        fund_mapping=fund_map,
    )
    planned_bucket = BucketAssetClassSplit(
        bucket="long_term",
        equity=600000,
        debt=200000,
        others=0,
        equity_pct=75,
        debt_pct=25,
        others_pct=0,
    )
    actual_bucket = BucketAssetClassSplit(
        bucket="long_term",
        equity=500000,
        debt=300000,
        others=0,
        equity_pct=62.5,
        debt_pct=37.5,
        others_pct=0,
    )
    breakdown = AssetClassBreakdown(
        planned=AssetClassSplitBlock(
            per_bucket=[planned_bucket],
            equity_total=600000,
            debt_total=200000,
            others_total=0,
            equity_total_pct=75,
            debt_total_pct=25,
            others_total_pct=0,
        ),
        actual=AssetClassSplitBlock(
            per_bucket=[actual_bucket],
            equity_total=500000,
            debt_total=300000,
            others_total=0,
            equity_total_pct=62.5,
            debt_total_pct=37.5,
            others_total_pct=0,
        ),
        actual_sum_matches_grand_total=True,
        subgroups=SubgroupBreakdown(
            planned=[
                SubgroupBucketSplit(
                    bucket="long_term",
                    subgroups=[SubgroupBucketAllocation(subgroup="low_beta_equities", amount=600000, pct_of_bucket=75)],
                )
            ],
            actual=[
                SubgroupBucketSplit(
                    bucket="long_term",
                    subgroups=[SubgroupBucketAllocation(subgroup="low_beta_equities", amount=500000, pct_of_bucket=62.5)],
                )
            ],
        ),
    )
    return GoalAllocationOutput(
        client_summary=ClientSummary(
            age=35,
            occupation="Salaried",
            effective_risk_score=7.0,
            total_corpus=800000,
            goals=[goal],
        ),
        bucket_allocations=[bucket],
        aggregated_subgroups=[subgroup],
        future_investments_summary=[bucket.future_investment],
        grand_total=800000,
        all_amounts_in_multiples_of_100=True,
        asset_class_breakdown=breakdown,
    )


def test_goal_type_removed_from_financial_goal_model() -> None:
    assert not hasattr(FinancialGoal, "goal_type")


def test_allocation_payload_uses_customer_label() -> None:
    payload = _allocation_output_to_jsonable(_build_output())
    assert payload["aggregated_subgroups"][0]["subgroup"] == "Large Cap"


def test_lean_goal_allocation_recommendation_row_holds_core_data() -> None:
    output = _build_output()
    payload = _allocation_output_to_jsonable(output)
    recommendation_row = GoalAllocationRecommendation(
        user_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        portfolio_id=None,
        chat_session_id=None,
        input_payload={"age": 35, "effective_risk_score": 7.0},
        output_payload=payload,
        total_investable_amount=float(output.grand_total),
        equity_amount=500000,
        debt_amount=300000,
        others_amount=0,
        equity_pct=62.5,
        debt_pct=37.5,
        others_pct=0,
        suggested_funds=[
            {
                "asset_class": "equity",
                "asset_subgroup": "low_beta_equities",
                "sub_category": "Large Cap",
                "recommended_fund": "Sample Fund",
                "isin": "INF000000001",
                "amount": 500000,
            }
        ],
        suggested_funds_total_amount=500000,
    )

    assert recommendation_row.input_payload["age"] == 35
    assert float(recommendation_row.total_investable_amount) == 800000
    assert float(recommendation_row.equity_amount) == 500000
    assert float(recommendation_row.debt_amount) == 300000
    assert float(recommendation_row.others_amount) == 0
    assert float(recommendation_row.equity_pct) == 62.5
    assert float(recommendation_row.suggested_funds_total_amount) == 500000
    assert recommendation_row.suggested_funds[0]["recommended_fund"] == "Sample Fund"
    assert recommendation_row.output_payload["bucket_allocations"][0]["bucket"] == "long_term"
