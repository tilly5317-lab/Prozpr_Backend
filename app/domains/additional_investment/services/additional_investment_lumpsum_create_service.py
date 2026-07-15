"""Create a one-time lump-sum deployment plan from the Invest page.

The Invest → Lump sum page's "Plan lump sum" action runs the SAME
additional-investment engine chat uses, with ``cadence=lumpsum`` and no chat
session — the twin of ``additional_investment_create_service`` (SIP). Lumpsum
triggers holdings-aware DEFICIT FILL inside the engine adapter: it computes the
customer's ideal portfolio for their goals INCLUDING the new money, compares it
with what they hold today, and steers the money into the largest gaps. Those
facts flow back as ``outcome.deficit_facts`` (persisted on the run) and become
the page's per-fund reasoning + alignment section.

Unlike the SIP create, a lumpsum is a one-off — it is NOT the customer's monthly
SIP, so nothing is written to ``starting_monthly_investment`` (the engine adapter
only syncs that on ``sip_monthly`` runs). On success we re-read the persisted run
via ``get_latest_lumpsum_plan`` so the create response is byte-for-byte the read
shape.

Only ``action='add'`` is supported: the additional-investment engine is BUY-only
(it never sells), so ``action='withdraw'`` is rejected with a customer-facing
message rather than pretending to raise cash.
"""

from __future__ import annotations

import uuid

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.additional_investment.schemas import LumpsumPlanResponse
from app.domains.additional_investment.services.additional_investment_read_service import (
    get_latest_lumpsum_plan,
)
from app.domains.additional_investment.services.ainv_engine.service import (
    compute_additional_investment_result,
)
from app.domains.ai_engine.common import ensure_ai_agents_path
from app.domains.ai_engine.turn_context import TurnContext
from app.domains.identity.models.user import User

ensure_ai_agents_path()

from additional_investment.models import (  # type: ignore[import-not-found]  # noqa: E402
    Cadence,
)

_MSG_PERSIST_FAILED = (
    "We worked out your lump-sum plan but couldn't save it just now. Please try "
    "again in a moment."
)
_MSG_WITHDRAW_UNSUPPORTED = (
    "Withdrawals aren't available here yet — this planner deploys fresh money to "
    "invest. To raise cash by redeeming holdings, use the Rebalancing flow."
)


async def create_lumpsum_plan_for_user(
    db: AsyncSession,
    user: User,
    *,
    acting_user_id: uuid.UUID,
    amount_inr: float,
    action: str = "add",
) -> LumpsumPlanResponse:
    """Compute + persist a fresh lump-sum plan, then return it in read shape.

    Runs the chat compute path (``cadence=lumpsum``) without a chat session.
    Raises ``HTTPException`` with the engine's customer-facing gate text (422)
    when the profile is too incomplete to plan, when ``action='withdraw'`` (not
    supported), or 500 when the recommendation could not be persisted. The
    caller's request session is committed here on success.
    """
    if action != "add":
        # BUY-only engine: never fabricate a redemption plan. The frontend
        # disables the Withdraw toggle, so this only guards a stray request.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=_MSG_WITHDRAW_UNSUPPORTED,
        )

    ctx = TurnContext(
        user_ctx=user,
        user_question=f"Invest a one-time lump sum of {amount_inr:.0f} rupees",
        conversation_history=[],
        client_context=None,
        session_id=uuid.uuid4(),  # unused by the compute path; not persisted
        db=db,
        effective_user_id=acting_user_id,
        last_agent_runs={},
        active_intent=None,
        chat_overrides=None,
    )

    outcome = await compute_additional_investment_result(
        user,
        ctx.user_question,
        db=db,
        acting_user_id=acting_user_id,
        chat_session_id=None,  # non-chat create: run persists, unlinked to a session
        deploy_amount_inr=amount_inr,
        cadence=Cadence.LUMPSUM,
        chat_ctx=ctx,
        persist=True,
    )

    if outcome.blocking_message:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=outcome.blocking_message,
        )
    if outcome.run_id is None:
        # Persistence is best-effort inside compute; a None id here means the
        # write failed (already logged there). Never commit a half-written plan.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=_MSG_PERSIST_FAILED,
        )

    await db.commit()

    # Re-read so the create response is byte-for-byte the read shape (the new run
    # is the latest lumpsum run for this user).
    return await get_latest_lumpsum_plan(db, acting_user_id)


__all__ = ["create_lumpsum_plan_for_user"]
