"""Existing allocation persistence must stamp recommendation_type=ALLOCATION."""

from __future__ import annotations

import asyncio
import unittest
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from app.models.rebalancing import (
    RebalancingRecommendation,
    RecommendationType,
)
from app.services import allocation_recommendation_persist as persist_mod


class _FakePortfolio:
    def __init__(self):
        self.id = uuid.uuid4()


def _build_fake_output() -> MagicMock:
    """Minimal stub of ``GoalAllocationOutput`` for the persist helper.

    The helper only touches ``model_dump``, ``aggregated_subgroups``, and
    ``asset_class_breakdown``. Everything else can stay as a Mock.
    """
    output = MagicMock()
    output.model_dump = MagicMock(return_value={"aggregated_subgroups": []})
    output.aggregated_subgroups = []
    # ``asset_class_breakdown`` is checked for ``is None``; provide one with
    # zeros to short-circuit through the easy code path.
    acb = MagicMock()
    acb.actual.equity_total_pct = 0.0
    acb.actual.debt_total_pct = 0.0
    acb.actual.others_total_pct = 0.0
    output.asset_class_breakdown = acb
    return output


class AllocationPersistDiscriminatorTests(unittest.TestCase):

    def test_allocation_persist_stamps_allocation_type(self):
        """``persist_goal_allocation_recommendation`` must stamp
        ``recommendation_type=ALLOCATION`` and leave ``source_allocation_id``
        unset (allocation rows do not reference a source allocation)."""
        added: list[object] = []
        db = MagicMock()
        db.add = MagicMock(side_effect=lambda row: added.append(row))
        db.flush = AsyncMock()

        fake_portfolio = _FakePortfolio()
        output = _build_fake_output()

        with patch.object(
            persist_mod,
            "get_or_create_primary_portfolio",
            new=AsyncMock(return_value=fake_portfolio),
        ):
            asyncio.run(
                persist_mod.persist_goal_allocation_recommendation(
                    db, uuid.uuid4(), output,
                )
            )

        rec_rows = [r for r in added if isinstance(r, RebalancingRecommendation)]
        self.assertEqual(len(rec_rows), 1)
        rec = rec_rows[0]
        self.assertEqual(rec.recommendation_type, RecommendationType.ALLOCATION)
        self.assertIsNone(rec.source_allocation_id)


if __name__ == "__main__":
    unittest.main()
