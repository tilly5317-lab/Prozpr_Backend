"""Create a monthly-SIP deployment plan from outside chat (the Invest page).

SIP plans are normally produced inside a chat turn (the ``additional_investment``
intent). This service lets the Invest page's "Start a SIP" action run the SAME
compute + persist path without a chat session: it wraps the loaded AI ``User``
graph in a minimal ``TurnContext`` and calls ``compute_additional_investment_result``
with ``cadence=sip_monthly`` and ``persist=True`` (a null ``chat_session_id`` —
the run persists, just unlinked to a chat session).

The compute path reads only ``ctx.user_ctx`` (and ``ctx.chat_overrides``, kept
None); ``session_id`` / ``db`` / history on the context are unused here and are
populated only to satisfy the frozen dataclass. On the happy path we re-read the
persisted run via ``get_latest_sip_plan`` so the create response matches the read
shape exactly.

The compute path also writes the monthly amount to the canonical
``personal_finance_profiles.starting_monthly_investment`` in this same
transaction, so a SIP started here reaches the goal planner without the client
following up with a ``PUT /profile/personal-finance``. That happens for EVERY
``sip_monthly`` run (chat included), not just this endpoint — see
``compute_additional_investment_result``.
"""

from __future__ import annotations

import uuid

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.additional_investment.schemas import SipPlanResponse
from app.domains.additional_investment.services.additional_investment_read_service import (
    get_latest_sip_plan,
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
    "We worked out your SIP but couldn't save it just now. Please try again in a "
    "moment."
)


async def create_sip_plan_for_user(
    db: AsyncSession,
    user: User,
    *,
    acting_user_id: uuid.UUID,
    monthly_amount_inr: float,
) -> SipPlanResponse:
    """Compute + persist a fresh monthly SIP plan, then return it in read shape.

    Runs the chat compute path without a chat session. Raises ``HTTPException``
    with the engine's customer-facing gate text (422) when the profile is too
    incomplete to plan, or 500 when the recommendation could not be persisted.
    The caller's request session is committed here on success.
    """
    ctx = TurnContext(
        user_ctx=user,
        user_question=f"Start a monthly SIP of {monthly_amount_inr:.0f} rupees",
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
        deploy_amount_inr=monthly_amount_inr,
        cadence=Cadence.SIP_MONTHLY,
        chat_ctx=ctx,
        persist=True,
    )

    if outcome.blocking_message:
        # Incomplete profile / engine pre-check — surface the customer-facing gate
        # so the Invest page can tell the user what to complete.
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

    # Commits the run AND the canonical `starting_monthly_investment` the compute
    # path synced alongside it (see compute_additional_investment_result) — they
    # share this transaction, so the plan and the amount land together.
    await db.commit()

    # Re-read so the create response is byte-for-byte the read shape (the new run
    # is the latest sip_monthly run for this user).
    return await get_latest_sip_plan(db, acting_user_id)


__all__ = ["create_sip_plan_for_user"]
