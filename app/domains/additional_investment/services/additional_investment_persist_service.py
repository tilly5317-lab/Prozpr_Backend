"""Persist an additional-investment engine result across the normalized
``additional_investment_*`` tables.

A run always references the persisted asset-allocation run whose per-bucket
subgroups it deployed fresh money into -- ``source_allocation_run_id`` is
required.

Money is plain ``float`` (rupees), matching the allocation family this engine
composes with (practical_asset_allocation), NOT the ``Decimal`` used by
Rebalancing: floats flow straight into the ``Numeric(18, 2)`` columns. There
is no tax-lot arithmetic here, so ``_to_decimal`` is deliberately NOT used.

Commit-free: the caller owns the transaction (mirrors
``persist_rebalancing_recommendation``).
"""

from __future__ import annotations

import uuid
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.additional_investment.models import (
    AdditionalInvestmentBuy,
    AdditionalInvestmentRun,
    AdditionalInvestmentTarget,
)
from app.domains.ai_engine.common import ensure_ai_agents_path
from app.domains.portfolio.services.portfolio_service import (
    get_or_create_primary_portfolio,
)

ensure_ai_agents_path()

from additional_investment.models import (  # type: ignore[import-not-found]  # noqa: E402
    AdditionalInvestmentInput,
    AdditionalInvestmentOutput,
)


async def persist_additional_investment_recommendation(
    db: AsyncSession,
    user_id: uuid.UUID,
    output: AdditionalInvestmentOutput,
    *,
    source_allocation_run_id: uuid.UUID,
    chat_session_id: Optional[uuid.UUID] = None,
    user_question: Optional[str] = None,
    request: Optional[AdditionalInvestmentInput] = None,
    request_extras: Optional[dict[str, Any]] = None,
    saved_investment_preference_id: Optional[uuid.UUID] = None,
) -> uuid.UUID:
    """Write the engine output and return the new ``AdditionalInvestmentRun`` id.

    BUY-only / write-once: there is no status-lifecycle field to set (contrast
    ``RebalancingRun.status``).
    """
    # ``AINV_ENGINE_VERSION`` lives in the engine adapter (ainv_engine/service.py).
    # Import it lazily so this module never top-level-imports service.py: Plan
    # 3b-T4 makes service.py top-level-import THIS module, and a mutual top-level
    # import would cycle.
    from app.domains.additional_investment.services.ainv_engine.service import (
        AINV_ENGINE_VERSION,
    )

    portfolio = await get_or_create_primary_portfolio(db, user_id)

    # ``request`` is optional but recommended so the per-call engine input is
    # captured for audit. Serialise to JSON-safe primitives for the JSONB column.
    # ``request_extras`` (deficit-fill mode metadata) is MERGED over the dump —
    # the stored dict is a superset of the engine input, no longer a pure
    # round-trippable model dump (spec 2026-07-03).
    request_input: Optional[dict[str, Any]] = (
        request.model_dump(mode="json") if request is not None else None
    )
    if request_extras:
        request_input = {**(request_input or {}), **request_extras}

    run = AdditionalInvestmentRun(
        user_id=user_id,
        portfolio_id=portfolio.id,
        chat_session_id=chat_session_id,
        source_allocation_run_id=source_allocation_run_id,
        engine_version=AINV_ENGINE_VERSION,
        # SAEnum columns persist the pydantic ``.value`` strings.
        target_bucket=output.target_bucket.value,
        cadence=output.cadence.value,
        # Floats straight into Numeric(18, 2) -- NO _to_decimal.
        deploy_amount_inr=output.deploy_amount_inr,
        deployed_inr=output.deployed_inr,
        undeployed_inr=output.undeployed_inr,
        user_question=user_question,
        request_input=request_input,
        saved_investment_preference_id=saved_investment_preference_id,
    )
    db.add(run)
    await db.flush()  # assign run.id before parenting children

    for target in output.per_subgroup_target:
        db.add(
            AdditionalInvestmentTarget(
                run_id=run.id,
                subgroup=target.subgroup,
                ratio=target.ratio,
                target_inr=target.target_inr,
            )
        )

    # ``rank`` + ``scheme_code`` are not on the engine ``FundBuy``; recover them
    # by joining each buy's isin against the request's ``ranked_funds`` (every
    # buy's isin is a ranked fund by construction), so ``request`` is required
    # whenever there are buys.
    ranked_by_isin = {
        rf.isin: rf for rf in (request.ranked_funds if request is not None else [])
    }

    for buy in output.buys:
        ranked = ranked_by_isin[buy.isin]
        db.add(
            AdditionalInvestmentBuy(
                run_id=run.id,
                recommended_fund=buy.recommended_fund,
                isin=buy.isin,
                sub_category=buy.sub_category,
                asset_subgroup=buy.asset_subgroup,
                rank=ranked.rank,
                scheme_code=ranked.scheme_code,
                amount_inr=buy.amount_inr,
                # Already None for lumpsum, set for sip_monthly by the engine.
                monthly_amount_inr=buy.monthly_amount_inr,
                reason=buy.reason,
            )
        )

    await db.flush()
    return run.id
