"""AI bridge — `ailax_flow.py`.

Sits between FastAPI services/routers and the ``AI_Agents/src`` packages (added to `sys.path` via ``ensure_ai_agents_path``). Handles env keys, async/thread boundaries, and user-context mapping. Ideal mutual fund allocation is invoked from here using ``Ideal_asset_allocation`` inside the app layer (e.g. ``ideal_allocation_runner``) so `AI_Agents` files stay untouched.
"""


from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from enum import Enum

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.services.ai_bridge.asset_allocation_service import (
    compute_allocation_result,
    format_allocation_chat_brief,
)


@dataclass
class AilaxSpineResult:
    """Chat markdown plus optional DB row ids when an ideal plan was persisted."""

    text: str
    rebalancing_recommendation_id: uuid.UUID | None = None
    portfolio_allocation_snapshot_id: uuid.UUID | None = None


class SpineMode(str, Enum):
    """Branches after portfolio-style intents (wording heuristic)."""

    FULL = "full"
    CASH_IN = "cash_in"
    CASH_OUT = "cash_out"
    DRIFT_CHECK = "drift_check"
    REBALANCE = "rebalance"


def detect_spine_mode(user_question: str) -> SpineMode:
    q = user_question.lower()

    if re.search(
        r"\b(withdraw|redemption|redeem|sell\s+mf|take\s+out|need\s+cash|exit\s+load|"
        r"stp\s+out|swp|take\s+money)\b",
        q,
    ):
        return SpineMode.CASH_OUT

    if re.search(
        r"\b(sip\b|lump\s*sum|invest|subscribe|buy\s+mf|add\s+funds|"
        r"put\s+money|fresh\s+invest|allocate\s+more|contribute)\b",
        q,
    ):
        return SpineMode.CASH_IN

    if re.search(
        r"\b(drift|off[\s-]?target|deviation|misaligned|not\s+aligned|"
        r"away\s+from\s+target|allocation\s+gap)\b",
        q,
    ):
        return SpineMode.DRIFT_CHECK

    if re.search(r"\b(rebalanc|rebalance|bring\s+back|align\s+portfolio)\b", q):
        return SpineMode.REBALANCE

    return SpineMode.FULL


async def build_ailax_spine(
    user: User,
    user_question: str,
    mode: SpineMode,
    *,
    db: AsyncSession | None = None,
    persist_recommendation: bool = False,
    acting_user_id: uuid.UUID | None = None,
    chat_session_id: uuid.UUID | None = None,
) -> AilaxSpineResult:
    """
    One short assistant message: narrative + target mix + a few concrete moves.
    When ``persist_recommendation`` and ``db`` are set, stores the plan for
    ``GET /portfolio/recommended-plan`` and ``GET /rebalancing``.
    """
    outcome = await compute_allocation_result(
        user,
        user_question,
        db=db,
        persist_recommendation=persist_recommendation,
        acting_user_id=acting_user_id,
        chat_session_id=chat_session_id,
        spine_mode=mode.value,
    )
    if outcome.blocking_message:
        return AilaxSpineResult(text=outcome.blocking_message)
    if not outcome.result:
        return AilaxSpineResult(
            text=(
                "I couldn’t produce an allocation summary just now. "
                "Check your profile is complete and try again in a moment."
            )
        )

    result = outcome.result
    rp = getattr(user, "risk_profile", None)
    cat = getattr(rp, "risk_category", None) if rp else None
    if cat:
        header = f"Using your saved risk profile (**{cat}**).\n\n"
    else:
        header = "Complete your risk questionnaire when you can — guidance below is broader without it.\n\n"

    body = format_allocation_chat_brief(result, mode.value)
    return AilaxSpineResult(
        text=header + body,
        rebalancing_recommendation_id=outcome.rebalancing_recommendation_id,
        portfolio_allocation_snapshot_id=outcome.allocation_snapshot_id,
    )
