"""Pre-flow guard: does this turn belong to the customer's plan?

One gate where there were two. ``profile_gate`` asked "do we have the inputs
the engine needs?" and ``goal_gate`` asked "is a goal half-built?", and because
neither could see the other's state they had to negotiate: the profile gate
stood down entirely whenever a goal draft was open, because a soft ask for
``target_corpus`` had once opened silently mid-goal and swallowed the
customer's "50 lakhs down, 11% over 5 years" as an answer about risk tolerance.
With one store behind one gate that whole class of bug is gone — an open ask
and an open draft are read together, and the same flow owns both.

Sibling of ``portfolio_gate``, same shape and the same instinct: it sits
between classification and flow selection, and it FAILS OPEN. Wrongly telling a
customer we are missing something we have is worse than letting the engine
answer.

Exits, in precedence order:

  1. Something is already open on this session — a question or a goal being
     built → the turn belongs to planning, whatever the classifier said.
     (Checked first, because mid-thread fragments — "yes, 50 lakhs down", "no
     everything's the same" — genuinely are ambiguous out of context, and the
     context lives in the row, not in the sentence.)
  2. The classifier said ``financial_planning`` → planning owns it.
  3. Another intent is missing a hard-required input → spend this turn asking
     for it, and remember which intent to resume.
  4. Nothing blocking → no-op; the engine flow runs as before.

What the gate never does: gate a read-only intent, ask more than
``MAX_ASKS_PER_SESSION`` times, or re-ask a field inside its deferral window.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.profile.services.profile_completeness_service import (
    gaps_for_intent,
    load_snapshot,
    next_field_to_ask,
)
from app.domains.profile.services.profile_field_registry import NEVER_GATED_INTENTS

logger = logging.getLogger(__name__)

PLANNING_INTENT = "financial_planning"

# Intents allowed to interrupt an open planning thread. A customer who breaks
# off to ask what they hold, or how mid-caps are doing, should get that answered
# — the thread is still there when they come back to it.
#
# ``out_of_scope`` and ``stock_advice`` are deliberately NOT here, though the
# old goal gate listed them. An unanchored fragment is precisely what the
# classifier labels ``out_of_scope``: "50 lakhs down" and "no, everything's the
# same" carry no topic on their own, and letting that label steal a turn we
# opened with a direct question is how an answer goes missing. Anything that
# genuinely is off-topic still gets answered — the module hands the turn back
# (``handoff``) once it reads the message and finds nothing about the plan in
# it, which is a decision made with the thread in view rather than without it.
_ALLOWED_TO_INTERRUPT: frozenset[str] = frozenset(
    {
        "portfolio_query",
        "mutual_fund_query",
        "general_market_query",
    }
)


@dataclass(frozen=True)
class PlanningDirective:
    """What the gate decided. Attached to ``TurnContext.planning_directive``."""

    # An open question, when there is one — the module consumes it.
    pending_ask: Any = None
    # An open goal draft, when there is one.
    draft: Any = None
    # Set when we are opening a NEW ask because another intent is blocked.
    field_key: str | None = None
    # The intent whose turn is being spent, replayed once the block clears.
    resume_intent: str | None = None
    # The classifier put this turn in planning's hands on its own.
    claimed_by_intent: bool = False

    @property
    def routes_to_planning(self) -> bool:
        return (
            self.pending_ask is not None
            or self.draft is not None
            or self.field_key is not None
            or self.claimed_by_intent
        )

    @property
    def reason(self) -> str:
        if self.pending_ask is not None:
            return f"consuming the answer to {self.pending_ask.field_key}"
        if self.draft is not None:
            return f"continuing the goal being built ({self.draft.stage})"
        if self.field_key is not None:
            return f"asking for {self.field_key}"
        return "the customer is working on their plan"


async def evaluate(
    db: AsyncSession | None,
    user_id: uuid.UUID | None,
    session_id: uuid.UUID | None,
    intent: str,
) -> PlanningDirective | None:
    """Decide whether this turn belongs to financial planning.

    Returns ``None`` for "carry on as normal" — including on any error.
    """
    if db is None or user_id is None or session_id is None:
        return None

    try:
        from app.domains.financial_planning.services import planning_state as state

        # ---- 1. An open thread outranks the classifier --------------------
        if intent not in _ALLOWED_TO_INTERRUPT:
            pending = await state.get_pending(db, session_id)
            draft = await state.get_open_draft(db, session_id)
            if pending is not None or draft is not None:
                return PlanningDirective(
                    pending_ask=pending,
                    draft=draft,
                    resume_intent=(pending.resume_intent if pending else None),
                )

        # ---- 2. The classifier put it here --------------------------------
        if intent == PLANNING_INTENT:
            return PlanningDirective(claimed_by_intent=True)

        # ---- 3. Would this intent block? ----------------------------------
        if intent in NEVER_GATED_INTENTS:
            return None

        blocking = await next_blocking_field(db, user_id, session_id, intent)
        if blocking is None:
            return None
        return PlanningDirective(field_key=blocking, resume_intent=intent)

    except Exception:
        logger.exception(
            "planning_gate check failed (user=%s intent=%s); running the flow anyway",
            user_id,
            intent,
        )
        return None


async def next_blocking_field(
    db: AsyncSession,
    user_id: uuid.UUID,
    session_id: uuid.UUID,
    requirement: str,
) -> str | None:
    """The one field to ask about before ``requirement`` can honestly run.

    ``requirement`` is a key in ``profile_field_registry.REQUIREMENTS`` — an
    intent name for most callers, and ``financial_planning_projection`` for the
    projection, which needs the customer's income where merely describing a
    wedding does not.

    ``None`` means "nothing to ask": either nothing is missing, the ask budget
    for this conversation is spent, or every blocking field has already been
    declined — in which case an honest partial answer beats re-asking a question
    they said no to.
    """
    from app.domains.financial_planning.services import planning_state as state

    if await state.asks_this_session(db, session_id) >= state.MAX_ASKS_PER_SESSION:
        # Budget spent. The completeness indicator does the nudging from here.
        return None

    gaps = gaps_for_intent(requirement, await load_snapshot(db, user_id))
    if not gaps.hard_missing:
        return None

    quiet = await state.deferred_field_keys(db, user_id)
    declined = await state.hard_declined_keys(db, user_id)
    askable = [k for k in gaps.hard_missing if k not in quiet and k not in declined]
    if not askable:
        logger.info(
            "planning_gate: all blocking fields declined for user=%s requirement=%s; "
            "running anyway",
            user_id,
            requirement,
        )
        return None

    chosen = next_field_to_ask(askable)
    return chosen.key if chosen is not None else None


__all__ = [
    "PLANNING_INTENT",
    "PlanningDirective",
    "evaluate",
    "next_blocking_field",
]
