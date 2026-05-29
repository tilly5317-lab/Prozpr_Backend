"""Shared types for the ai_engine services layer.

The brain runs a chat turn as a *sequence of AI modules*. Two DTOs flow
through that loop:

- ``IntentDecision`` — what the intent classifier produces (the routing key
  + the raw classifier output, kept so downstream modules that want the full
  shape — e.g. general_chat — can use it without re-classifying).

- ``ModuleOutput`` — the uniform return value of every AI module service.
  ``ChatBrain.run_turn`` collects one ``ModuleOutput`` per module in an
  ``outputs`` dict and threads that dict into the next module so e.g. the
  rebalancing module can read the asset allocation it should rebalance to.

Architectural rule: each AI module name in ``AIModule`` maps to exactly one
domain service. Nothing else in the codebase imports the matching
``AI_Agents.*`` orchestrator — go through the module service.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Awaitable, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from app.domains.ai_engine.services.chat_orchestrator.turn_context import TurnContext
    from app.domains.ai_engine.services.chat_orchestrator.types import ChatTurnInput


# ---------------------------------------------------------------------------
# Intent classifier result
# ---------------------------------------------------------------------------

@dataclass
class IntentDecision:
    """Routing key + the raw classifier output."""

    name: str
    confidence: float
    reasoning: str | None = None
    # Raw IntentClassification pydantic from AI_Agents. Modules that want the
    # full shape (sentiment slots, etc.) read this instead of classifying again.
    raw: Any = None


# ---------------------------------------------------------------------------
# AI modules — the canonical names brain SEQUENCES are built from.
# Each name has exactly one owning domain + one *_module_service.py file.
# ---------------------------------------------------------------------------

class AIModule(StrEnum):
    """The AI modules the brain can run as part of a turn.

    Owners (the only place that may import the matching AI_Agents orchestrator):

      asset_allocation   →  app/domains/asset_allocation/services/asset_allocation_module_service.py
      rebalancing        →  app/domains/rebalancing/services/rebalancing_module_service.py
      cashflow           →  app/domains/cashflow/services/cashflow_module_service.py
      portfolio_query    →  app/domains/portfolio/services/portfolio_query_module_service.py
      market_commentary  →  app/domains/market_commentary/services/market_commentary_module_service.py
      intent_classifier  →  app/domains/intent_classifier/services/intent_classifier_service.py
      general_chat       →  app/domains/ai_engine/services/general_chat_module_service.py
    """

    INTENT_CLASSIFIER = "intent_classifier"
    ASSET_ALLOCATION  = "asset_allocation"
    REBALANCING       = "rebalancing"
    CASHFLOW          = "cashflow"
    PORTFOLIO_QUERY   = "portfolio_query"
    MARKET_COMMENTARY = "market_commentary"
    GENERAL_CHAT      = "general_chat"


# ---------------------------------------------------------------------------
# Module output — the uniform shape every module service returns.
# ---------------------------------------------------------------------------

@dataclass
class ModuleOutput:
    """Result of one AI module run.

    The brain collects one of these per module in an ``outputs: dict[str,
    ModuleOutput]`` and hands the dict to the next module. So the rebalancing
    module can do::

        prior_aa = prior_outputs[AIModule.ASSET_ALLOCATION].payload
        # ...rebalance against prior_aa...

    Fields:
      text          — formatted assistant message (only the LAST module in the
                      sequence typically populates this; earlier modules
                      return payload-only).
      payload       — the agent's structured output (e.g. an Allocation, a
                      RebalancingPlan, a CashflowProjection). Used by later
                      modules in the sequence.
      persisted_run_id / snapshot_id / rebalancing_recommendation_id —
                      IDs of rows the module just wrote, surfaced back to the
                      HTTP layer.
      chart_payloads — frontend-ready chart specs.
      side_effects  — free-form dict reserved for cross-turn gates (e.g.
                      ``{"awaiting_save": True}``).
    """

    text: str | None = None
    payload: Any = None
    persisted_run_id: uuid.UUID | None = None
    snapshot_id: uuid.UUID | None = None
    rebalancing_recommendation_id: uuid.UUID | None = None
    chart_payloads: list[dict[str, Any]] | None = None
    side_effects: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Uniform module service signature.
# ---------------------------------------------------------------------------

ModuleRunner = Callable[
    ["ChatTurnInput", "TurnContext", dict[str, ModuleOutput]],
    Awaitable[ModuleOutput],
]
"""Every ``*_module_service.run`` must satisfy this signature.

The third arg, ``prior_outputs``, is the running accumulator keyed by
``AIModule`` member. A module reads from it to consume earlier modules'
output (e.g. rebalancing reads asset_allocation), and the brain writes the
module's own return into it before invoking the next module.
"""
