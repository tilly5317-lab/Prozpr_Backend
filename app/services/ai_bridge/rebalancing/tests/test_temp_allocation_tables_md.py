"""TEMP(rebalancing-chat): full allocation table markdown tests."""

from __future__ import annotations

import unittest

from app.services.ai_bridge.asset_allocation.allocation_tables_md import (
    build_allocation_tables_markdown,
    is_allocation_output_complete,
)


class AllocationTablesMdTests(unittest.TestCase):
    def _sample_doc(self) -> dict:
        return {
            "grand_total": 1_000_000,
            "all_amounts_in_multiples_of_100": True,
            "client_summary": {
                "age": 30,
                "occupation": "salaried",
                "effective_risk_score": 7.0,
                "total_corpus": 1_000_000,
                "goals": [
                    {
                        "goal_name": "Retirement",
                        "time_to_goal_months": 120,
                        "amount_needed": 500_000,
                        "goal_priority": "non_negotiable",
                        "investment_goal": "wealth_creation",
                    },
                ],
            },
            "bucket_allocations": [
                {
                    "bucket": "long_term",
                    "goals": [{"goal_name": "Retirement", "time_to_goal_months": 120, "amount_needed": 500_000, "goal_priority": "non_negotiable", "investment_goal": "wealth_creation"}],
                    "total_goal_amount": 500_000,
                    "allocated_amount": 500_000,
                    "rationale": "ignored in tables",
                    "subgroup_amounts": {"debt_subgroup": 500_000},
                    "goal_rationales": {"Retirement": "r1"},
                    "future_investment": {"future_investment_amount": 0, "message": None},
                },
            ],
            "aggregated_subgroups": [
                {"subgroup": "debt_subgroup", "emergency": 0, "short_term": 0, "medium_term": 0, "long_term": 500_000, "total": 500_000},
            ],
            "asset_class_breakdown": {
                "planned": {
                    "equity_total": 400_000,
                    "debt_total": 500_000,
                    "others_total": 100_000,
                    "equity_total_pct": 40.0,
                    "debt_total_pct": 50.0,
                    "others_total_pct": 10.0,
                    "per_bucket": [{"bucket": "long_term", "equity": 400_000, "debt": 100_000, "others": 0, "equity_pct": 80.0, "debt_pct": 20.0, "others_pct": 0.0}],
                },
                "actual": {
                    "equity_total": 400_000,
                    "debt_total": 500_000,
                    "others_total": 100_000,
                    "equity_total_pct": 40.0,
                    "debt_total_pct": 50.0,
                    "others_total_pct": 10.0,
                    "per_bucket": [{"bucket": "long_term", "equity": 400_000, "debt": 100_000, "others": 0, "equity_pct": 80.0, "debt_pct": 20.0, "others_pct": 0.0}],
                },
            },
        }

    def test_has_all_table_sections(self):
        md = build_allocation_tables_markdown(self._sample_doc())
        for section in (
            "asset_allocation_runs",
            "asset_allocation_run_targets",
            "asset_allocation_aggregates",
            "asset_allocation_buckets",
            "asset_allocation_bucket_run_targets",
            "asset_allocation_bucket_asset_classes",
            "asset_allocation_bucket_subgroups",
            "aggregated_subgroups",
        ):
            self.assertIn(section, md)

    def test_complete_check(self):
        self.assertTrue(is_allocation_output_complete(self._sample_doc()))


if __name__ == "__main__":
    unittest.main()
