"""Pre-flow guard: intents that can't be answered without imported holdings.

Since CAMS became skippable at onboarding, a signed-in user can reach chat with
an empty portfolio. Intents like ``portfolio_query`` or ``rebalancing`` then run
a full engine over nothing and answer with a technical blocking message (or,
worse, an example-shaped answer the customer reads as their own numbers).

The brain checks this BEFORE picking a flow: no mutual-fund holdings + a
portfolio-dependent intent → return a short, honest reply asking for the CAS
statement, and flag the turn so the UI can render an "Add CAMS statement" CTA
next to it. Everything else (general chat, market questions, fund research, goal
planning) is unaffected — those answer fine without a portfolio.

``has_mf_holdings`` is the same existence check ``GET /rebalancing/readiness``
uses, so chat and the invest surfaces never disagree about whether a portfolio
exists.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# Intents that cannot produce an answer at all without imported holdings.
#
# ONLY rebalancing. Rebalancing computes per-fund buy/sell trades against what
# the customer actually owns — with nothing owned there is literally no trade
# to propose, so the engine can only fail.
#
# Deliberately NOT here:
#   * ``portfolio_query`` — "what do I hold?" is answerable with an empty
#     portfolio: the honest answer is that we have nothing on record yet, and
#     the query path can say so itself. Short-circuiting it meant a customer
#     asking about their SIPs or linked accounts hit a CAMS wall instead of an
#     answer.
#   * ``additional_investment`` — deploying fresh money (SIP / lump sum)
#     follows the ideal mix and needs no existing holdings.
PORTFOLIO_REQUIRED_INTENTS: frozenset[str] = frozenset({"rebalancing"})

# One reply, per intent, in PI's voice: say what's missing, why it matters for
# THIS question, and what to do about it. The UI attaches the upload CTA.
# ``portfolio_query`` is kept as the generic fallback wording for
# ``missing_portfolio_reply`` even though that intent is no longer gated.
_ASK: dict[str, str] = {
    "portfolio_query": (
        "I don't have your holdings yet, so I can't answer this on your real "
        "numbers — and I'd rather say that than guess."
    ),
    "rebalancing": (
        "Rebalancing plans trades against what you actually own, and I don't "
        "have your holdings yet."
    ),
}

_HOW = (
    "The quickest way is to add your CAMS/KFintech Consolidated Account "
    "Statement — I'll read every folio, holding and transaction in it, and from "
    "then on I can answer questions like this on your real numbers. If you don't "
    "have one handy, I can have CAMS email you a fresh copy from the upload "
    "screen.\n\n"
    "And if you're just getting started with us, you don't need a statement to "
    "begin — set up a SIP or invest a lump sum and I'll build your plan around "
    "that."
)


def missing_portfolio_reply(intent: str) -> str:
    """The customer-facing reply for a portfolio question with no portfolio."""
    return f"{_ASK.get(intent, _ASK['portfolio_query'])}\n\n{_HOW}"


async def has_mf_holdings(db: AsyncSession, user_id: uuid.UUID) -> bool:
    """True when the user has at least one normalized mutual-fund transaction.

    Mirrors ``GET /rebalancing/readiness``'s ``has_holdings``: MF holdings can
    only come from a CAS import, so one row is enough to prove a portfolio.
    """
    from app.domains.mutual_funds.models import MfTransaction

    row = (
        await db.execute(
            select(MfTransaction.id).where(MfTransaction.user_id == user_id).limit(1)
        )
    ).first()
    return row is not None


async def portfolio_data_missing(
    db: AsyncSession | None,
    user_id: uuid.UUID | None,
    intent: str,
) -> bool:
    """Should this turn short-circuit on "no portfolio yet"?

    Fails OPEN: any doubt (no session, no user, a DB hiccup) runs the normal
    flow, because wrongly telling a customer with a portfolio that we can't see
    it is far worse than letting the engine answer.
    """
    if intent not in PORTFOLIO_REQUIRED_INTENTS or db is None or user_id is None:
        return False
    try:
        return not await has_mf_holdings(db, user_id)
    except Exception:
        logger.exception("portfolio-presence check failed; running the flow anyway")
        return False
