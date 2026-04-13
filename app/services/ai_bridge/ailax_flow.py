"""AILAX spine: detect portfolio intent mode and run the allocation pipeline.

``detect_spine_mode`` inspects user wording (cash-in, cash-out, rebalance, etc.)
and ``build_ailax_spine`` orchestrates allocation + formatting into one chat reply.
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
    """Chat markdown + optional persisted plan IDs."""
    text: str
    rebalancing_recommendation_id: uuid.UUID | None = None
    portfolio_allocation_snapshot_id: uuid.UUID | None = None


class SpineMode(str, Enum):
    """Portfolio intent sub-modes derived from user wording."""
    FULL = "full"
    CASH_IN = "cash_in"
    CASH_OUT = "cash_out"
    DRIFT_CHECK = "drift_check"
    REBALANCE = "rebalance"


# Regex patterns keyed by mode, checked in priority order.
_MODE_PATTERNS: list[tuple[SpineMode, str]] = [
    (SpineMode.CASH_OUT,
     r"\b(withdraw|redemption|redeem|sell\s+mf|take\s+out|need\s+cash|exit\s+load|stp\s+out|swp|take\s+money)\b"),
    (SpineMode.CASH_IN,
     r"\b(sip\b|lump\s*sum|invest|subscribe|buy\s+mf|add\s+funds|put\s+money|fresh\s+invest|allocate\s+more|contribute)\b"),
    (SpineMode.DRIFT_CHECK,
     r"\b(drift|off[\s-]?target|deviation|misaligned|not\s+aligned|away\s+from\s+target|allocation\s+gap)\b"),
    (SpineMode.REBALANCE,
     r"\b(rebalanc|rebalance|bring\s+back|align\s+portfolio)\b"),
]


def detect_spine_mode(user_question: str) -> SpineMode:
    """Classify the user's question into a portfolio sub-mode."""
    q = user_question.lower()
    for mode, pattern in _MODE_PATTERNS:
        if re.search(pattern, q):
            return mode
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
    """Run allocation and return formatted chat markdown + optional persisted IDs."""
    outcome = await compute_allocation_result(
        user, user_question,
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
            text="I couldn't produce an allocation summary just now. "
                 "Check your profile is complete and try again in a moment."
        )

    # Prefix with risk profile status.
    rp = getattr(user, "risk_profile", None)
    cat = getattr(rp, "risk_category", None) if rp else None
    header = (
        f"Using your saved risk profile (**{cat}**).\n\n" if cat
        else "Complete your risk questionnaire when you can — guidance below is broader without it.\n\n"
    )

    body = format_allocation_chat_brief(outcome.result, mode.value)
    return AilaxSpineResult(
        text=header + body,
        rebalancing_recommendation_id=outcome.rebalancing_recommendation_id,
        portfolio_allocation_snapshot_id=outcome.allocation_snapshot_id,
    )
