"""Store the output of the practical (holdings-aware) allocation pipeline.

Narrow by design: takes the ``PracticalAllocationOutput`` produced by
``AI_Agents/src/practical_asset_allocation`` and writes a single
``practical_asset_allocation_runs`` header row — queryable scalars plus the
full engine output in ``result_payload``. Nothing else (no portfolio lookup,
no chat formatting).

The session is *flushed* but not committed — the caller (the chat router) owns
the transaction. Returns the new ``practical_asset_allocation_runs.id``.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.practical_asset_allocation.models import (
    PracticalAssetAllocationRun,
)
from app.domains.ai_engine.common import ensure_ai_agents_path

ensure_ai_agents_path()

from practical_asset_allocation.pipeline import (  # type: ignore[import-not-found]  # noqa: E402
    PracticalAllocationOutput,
)


async def persist_practical_allocation_run(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    output: PracticalAllocationOutput,
    chat_session_id: uuid.UUID | None = None,
    user_question: str | None = None,
    input_payload: dict[str, Any] | None = None,
    saved_investment_preference_id: uuid.UUID | None = None,
) -> uuid.UUID:
    """Write *output* into ``practical_asset_allocation_runs``; return the run id."""
    cs = output.client_summary
    rec = output.asset_class_breakdown.recommended
    cb = output.corpus_breakdown

    run = PracticalAssetAllocationRun(
        user_id=user_id,
        chat_session_id=chat_session_id,
        user_question=user_question,
        client_age=cs.age,
        client_occupation=str(cs.occupation) if cs.occupation is not None else None,
        client_effective_risk_score=float(cs.effective_risk_score),
        total_corpus=float(cs.total_corpus),
        grand_total=float(output.grand_total),
        all_amounts_in_multiples_of_100=output.all_amounts_in_multiples_of_100,
        equity_total=float(rec.equity_total),
        debt_total=float(rec.debt_total),
        others_total=float(rec.others_total),
        equity_total_pct=float(rec.equity_total_pct),
        debt_total_pct=float(rec.debt_total_pct),
        others_total_pct=float(rec.others_total_pct),
        mf_corpus=float(cb.mf_corpus_inr),
        non_mf_equity_input=float(cb.non_mf_equity_input_inr),
        non_mf_equity_actual=float(cb.non_mf_equity_actual_inr),
        excess_direct_stocks=float(cb.excess_direct_stocks_inr),
        elss_corpus=float(cb.elss_corpus_inr),
        rebalancing_corpus=float(cb.rebalancing_corpus_inr),
        max_non_mf_equity_pct_computed=float(cb.max_non_mf_equity_pct_computed),
        input_payload=input_payload or {},
        result_payload=output.model_dump(mode="json"),
        saved_investment_preference_id=saved_investment_preference_id,
    )
    db.add(run)
    await db.flush()  # assign run.id within the caller's transaction
    return run.id
