"""Tests for ``asset_allocation_*`` table persistence (repository + normalisation).

Schema reference: ``app/models/asset_allocation/TABLES.md``.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select

from app.models.asset_allocation.bucket import (
    AssetAllocationAggregate,
    AssetAllocationBucket,
    AssetAllocationBucketSubgroup,
)
from app.models.asset_allocation.run import AssetAllocationRun, AssetAllocationRunTarget
from app.services.ai_bridge.asset_allocation.persistence import (
    normalize_asset_allocation_engine_result,
    save_asset_allocation_from_engine_output,
)
from app.services.ai_bridge.rebalancing.cached_allocation import try_parse_asset_allocation_json


def _minimal_engine_allocation_document() -> dict:
    """Compact valid document matching the legacy pipeline JSON shape."""
    return {
        "client_summary": {
            "age": 35,
            "occupation": "salaried",
            "effective_risk_score": 6.5,
            "total_corpus": 300000.0,
            "goals": [
                {
                    "goal_name": "Emergency Fund",
                    "time_to_goal_months": 6,
                    "amount_needed": 300000.0,
                    "goal_priority": "non_negotiable",
                    "investment_goal": "safety",
                },
            ],
        },
        "bucket_allocations": [
            {
                "bucket": "emergency",
                "goals": [
                    {
                        "goal_name": "Emergency Fund",
                        "time_to_goal_months": 6,
                        "amount_needed": 300000.0,
                        "goal_priority": "non_negotiable",
                        "investment_goal": "safety",
                    },
                ],
                "total_goal_amount": 300000.0,
                "allocated_amount": 300000.0,
                "future_investment": {
                    "bucket": "emergency",
                    "future_investment_amount": 0.0,
                    "message": None,
                },
                "subgroup_amounts": {"debt_subgroup": 300000},
                "rationale": "Liquid corpus in short-duration debt.",
                "goal_rationales": {"Emergency Fund": "Six months expenses."},
            },
        ],
        "aggregated_subgroups": [
            {"subgroup": "debt_subgroup", "total": 300000},
        ],
        "grand_total": 300000.0,
        "all_amounts_in_multiples_of_100": True,
        "asset_class_breakdown": {
            "planned": {
                "equity_total": 0,
                "debt_total": 300000,
                "others_total": 0,
                "equity_total_pct": 0.0,
                "debt_total_pct": 100.0,
                "others_total_pct": 0.0,
                "per_bucket": [
                    {
                        "bucket": "emergency",
                        "equity": 0,
                        "debt": 300000,
                        "others": 0,
                        "equity_pct": 0.0,
                        "debt_pct": 100.0,
                        "others_pct": 0.0,
                    },
                ],
            },
            "actual": {
                "equity_total": 0,
                "debt_total": 300000,
                "others_total": 0,
                "equity_total_pct": 0.0,
                "debt_total_pct": 100.0,
                "others_total_pct": 0.0,
                "per_bucket": [
                    {
                        "bucket": "emergency",
                        "equity": 0,
                        "debt": 300000,
                        "others": 0,
                        "equity_pct": 0.0,
                        "debt_pct": 100.0,
                        "others_pct": 0.0,
                    },
                ],
            },
        },
    }


def test_normalize_accepts_wrapper_and_pydantic_shaped_dump() -> None:
    inner = _minimal_engine_allocation_document()
    assert normalize_asset_allocation_engine_result({"goal_allocation_output": inner}) == inner
    assert normalize_asset_allocation_engine_result(inner) == inner

    class _DummyModel:
        def model_dump(self, mode: str = "python") -> dict:
            return {"goal_allocation_output": inner}

    assert normalize_asset_allocation_engine_result(_DummyModel()) == inner


@pytest.mark.asyncio
async def test_save_writes_run_targets_buckets_rebalancing_payload(db_session, fixture_user) -> None:
    inner = _minimal_engine_allocation_document()
    run_id = await save_asset_allocation_from_engine_output(
        db_session,
        user_id=fixture_user.id,
        portfolio_id=None,
        chat_session_id=None,
        pipeline_source="asset_allocation_pydantic",
        spine_mode="test",
        user_question="allocate",
        input_payload={"k": "v"},
        engine_result={"goal_allocation_output": inner},
        financial_goal_ids_by_name=None,
    )
    await db_session.commit()

    assert isinstance(run_id, uuid.UUID)

    n_targets = (
        await db_session.execute(
            select(func.count()).select_from(AssetAllocationRunTarget).where(
                AssetAllocationRunTarget.run_id == run_id,
            )
        )
    ).scalar_one()
    assert int(n_targets) == 1

    n_sub = (
        await db_session.execute(
            select(func.count())
            .select_from(AssetAllocationBucketSubgroup)
            .join(
                AssetAllocationBucket,
                AssetAllocationBucketSubgroup.bucket_id == AssetAllocationBucket.id,
            )
            .where(AssetAllocationBucket.run_id == run_id)
        )
    ).scalar_one()
    assert int(n_sub) >= 1

    # Same shape rebalancing consumes when built from ORM (aggregated totals).
    agg = (
        await db_session.execute(
            select(
                AssetAllocationBucketSubgroup.subgroup,
                func.sum(AssetAllocationBucketSubgroup.actual_amount),
            )
            .join(
                AssetAllocationBucket,
                AssetAllocationBucketSubgroup.bucket_id == AssetAllocationBucket.id,
            )
            .where(AssetAllocationBucket.run_id == run_id)
            .group_by(AssetAllocationBucketSubgroup.subgroup)
        )
    ).all()
    payload = {
        "aggregated_subgroups": [
            {"subgroup": str(sg), "total": float(t or 0)} for sg, t in agg
        ],
    }
    view = try_parse_asset_allocation_json(payload)
    assert view is not None
    assert len(view.aggregated_subgroups) >= 1

    run = (
        await db_session.execute(select(AssetAllocationRun).where(AssetAllocationRun.id == run_id))
    ).scalar_one()
    assert run.input_payload == {"k": "v"}
    assert float(run.grand_total) == 300000.0

    # Verify aggregate rows (planned + actual) were created.
    n_agg = (
        await db_session.execute(
            select(func.count()).select_from(AssetAllocationAggregate).where(
                AssetAllocationAggregate.run_id == run_id,
            )
        )
    ).scalar_one()
    assert int(n_agg) == 2

    # Verify user_id is set on bucket subgroups.
    sub_user_ids = (
        await db_session.execute(
            select(AssetAllocationBucketSubgroup.user_id)
            .join(
                AssetAllocationBucket,
                AssetAllocationBucketSubgroup.bucket_id == AssetAllocationBucket.id,
            )
            .where(AssetAllocationBucket.run_id == run_id)
        )
    ).scalars().all()
    assert all(uid == fixture_user.id for uid in sub_user_ids)
