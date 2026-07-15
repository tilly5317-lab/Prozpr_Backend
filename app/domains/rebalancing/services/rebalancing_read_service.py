"""Read-side helpers over persisted rebalancing runs.

One consumer today: the additional-investment SIP path mirrors the BUY trades
of the customer's latest persisted rebalancing run (spec 2026-07-05). Lives in
the rebalancing domain because it queries this domain's tables; the ainv
service imports it (same direction as the existing fund_rank import — the
rebalancing domain never imports additional_investment, so no cycle).
"""

from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.rebalancing.models.rebalancing_run import RebalancingRun
from app.domains.rebalancing.models.rebalancing_trade import (
    RebalancingTrade,
    TradeAction,
)


async def latest_buy_trades_by_subgroup(
    db: AsyncSession, user_id: uuid.UUID
) -> Optional[tuple[uuid.UUID, dict[str, list[str]]]]:
    """``(run_id, subgroup -> BUY ISINs)`` from the user's latest rebalancing run.

    ``user_id`` is the acting (effective) user — the same identity the ainv
    path persists under, so a family member never sources funds from the
    primary account's run. The latest run is by ``created_at`` desc; status is
    deliberately ignored (product call, spec 2026-07-05: all plans are treated
    as accepted). ISINs within a subgroup are ordered by BUY ``amount_inr``
    desc and deduped (first occurrence wins). Returns None when the user has
    no run, or the latest run has no BUY trades — both mean "rank-1 fallback",
    and the caller must not stamp ``sip_rebal_run_id``.
    """
    run_id = (
        await db.execute(
            select(RebalancingRun.id)
            .where(RebalancingRun.user_id == user_id)
            .order_by(RebalancingRun.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if run_id is None:
        return None

    rows = (
        await db.execute(
            select(RebalancingTrade.asset_subgroup, RebalancingTrade.isin)
            .where(
                RebalancingTrade.run_id == run_id,
                RebalancingTrade.action == TradeAction.BUY,
            )
            .order_by(RebalancingTrade.amount_inr.desc())
        )
    ).all()

    by_subgroup: dict[str, list[str]] = {}
    for subgroup, isin in rows:
        bucket = by_subgroup.setdefault(subgroup, [])
        if isin not in bucket:
            bucket.append(isin)
    if not by_subgroup:
        return None
    return run_id, by_subgroup
