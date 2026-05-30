"""``ChatBrain`` — orchestrates one chat turn as a *sequence of AI modules*.

The flow (matches the chat-flow design doc):

    1. ``build_turn_context(turn)``         per-turn bag of state
    2. intent_classifier_service.run        →  IntentDecision (payload)
    3. SEQUENCES[intent.name]               →  ordered list of AI modules
    4. for each module:                     ←─┐
           output = module.run(turn, ctx,     │  outputs from prior modules
                                outputs)       │  are passed in so e.g.
           outputs[name] = output  ─────────────┘  rebalancing reads
                                                  asset_allocation's payload
    5. final = outputs[last module]
    6. finalize + telemetry → ChatBrainResult

Architectural rule (enforced by docstring contract on each module service):
each AI module name in ``AIModule`` maps to exactly one domain service —
nothing else in the codebase imports the matching ``AI_Agents.*`` orchestrator.

Adding a new intent is two edits:
  - one new ``*_module_service.py`` in the owning domain
  - one new row in ``_SEQUENCES`` below
"""

from __future__ import annotations

import asyncio
import logging
import time

import httpx

from app.domains.ai_engine.services.common import trace_line, trace_response_preview
from app.domains.ai_engine.services.chat_orchestrator.turn_context import (
    TurnContext, build_turn_context,
)
from app.domains.ai_engine.services.chat_orchestrator.types import (
    ChatBrainResult, ChatTurnInput,
)
from app.domains.ai_engine.services.types import (
    AIModule, IntentDecision, ModuleOutput, ModuleRunner,
)
from app.domains.chat.services.ai_module_telemetry import log_chat_turn_flow_summary
from app.domains.intent_classifier.services import intent_classifier_service

logger = logging.getLogger(__name__)

_CLASSIFIER_FAILURE_MESSAGE = (
    "Sorry — I couldn't process your request due to a technical issue. Please try again."
)
_INTENT_TIMEOUT_MESSAGE = (
    "That took longer than expected on my end — please try again in a moment."
)

# Best-effort caps so a slow downstream never keeps the user waiting forever.
_TELEMETRY_TIMEOUT_S = 5.0
_PER_MODULE_TIMEOUT_S = 60.0

# Sentinel for the legacy goal-planning canned redirect. The classifier still
# uses it to strip pre-cutover refusals out of historical chat. Kept here so
# the name stays importable from ``app.domains.ai_engine``.
_GOAL_PLANNING_SENTINEL = "isn't built into the chat yet"


# ---------------------------------------------------------------------------
# SEQUENCES — the new "switch". One entry per intent.name.
# ---------------------------------------------------------------------------

_SEQUENCES: dict[str, list[AIModule]] = {
    # Single-module intents
    "asset_allocation": [AIModule.ASSET_ALLOCATION],
    "portfolio_query":  [AIModule.PORTFOLIO_QUERY],
    "general_chat":     [AIModule.GENERAL_CHAT],

    # Multi-module intents — each module reads earlier modules' outputs.
    "rebalancing":          [AIModule.ASSET_ALLOCATION, AIModule.REBALANCING],   # AA target first, then rebalance to it
    "goal_planning":        [AIModule.CASHFLOW],                                 # cashflow_statement engine
    "general_market_query": [AIModule.MARKET_COMMENTARY, AIModule.GENERAL_CHAT], # macro doc → tailored reply
}

# Intents whose classifier output is itself the reply (canned out-of-scope /
# stock-advice redirects). No downstream module runs.
_CLASSIFIER_ONLY_INTENTS: frozenset[str] = frozenset({
    "out_of_scope",
    "stock_advice",
})

# Cross-turn gate: when the previous turn opened the save flow, route back to
# the cashflow module so the user's "yes save / no discard" answer lands on
# the right handler — regardless of what the classifier says this turn.
_AWAITING_SAVE_SEQUENCE: list[AIModule] = [AIModule.CASHFLOW]


# ---------------------------------------------------------------------------
# MODULES — name → uniform ``run(turn, ctx, prior)`` callable.
# Lazy-resolved at ChatBrain.__init__ so module imports don't run at app boot.
# ---------------------------------------------------------------------------

def _load_modules() -> dict[AIModule, ModuleRunner]:
    from app.domains.asset_allocation.services.asset_allocation_module_service import run as run_asset_allocation
    from app.domains.cashflow.services.cashflow_module_service          import run as run_cashflow
    from app.domains.market_commentary.services.market_commentary_module_service import run as run_market_commentary
    from app.domains.portfolio.services.portfolio_query_module_service  import run as run_portfolio_query
    from app.domains.rebalancing.services.rebalancing_module_service    import run as run_rebalancing
    from app.domains.ai_engine.services.general_chat_module_service     import run as run_general_chat

    return {
        AIModule.ASSET_ALLOCATION:  run_asset_allocation,
        AIModule.REBALANCING:       run_rebalancing,
        AIModule.CASHFLOW:          run_cashflow,
        AIModule.PORTFOLIO_QUERY:   run_portfolio_query,
        AIModule.MARKET_COMMENTARY: run_market_commentary,
        AIModule.GENERAL_CHAT:      run_general_chat,
    }


def _is_llm_auth_failure(exc: BaseException) -> bool:
    """Anthropic/OpenAI rejected credentials — expected until .env keys are valid."""
    if isinstance(exc, httpx.HTTPStatusError) and exc.response is not None:
        if exc.response.status_code == 401:
            return True
    msg = str(exc).lower()
    return (
        "401" in msg
        and ("unauthorized" in msg or "invalid x-api-key" in msg or "authentication_error" in msg)
    ) or ("invalid x-api-key" in msg)


# ---------------------------------------------------------------------------
# ChatBrain
# ---------------------------------------------------------------------------

class ChatBrain:
    """Orchestrates one chat turn. Stateless: safe to instantiate per request."""

    def __init__(self) -> None:
        # Resolved once per instance so each turn doesn't pay the import cost.
        self._modules = _load_modules()

    async def run_turn(self, turn: ChatTurnInput) -> ChatBrainResult:
        sid = turn.session_id
        uid = turn.effective_user_id
        db  = turn.db
        flow: list[str] = []
        t_all = time.perf_counter()

        intent: IntentDecision | None = None
        outputs: dict[str, ModuleOutput] = {}

        try:
            trace_line("--- ChatBrain.run_turn ---")
            trace_line(f"user message: {turn.user_question}")

            # ---- 1. Per-turn context ----------------------------------------
            ctx: TurnContext = await build_turn_context(turn)
            trace_line(
                f"turn_context: last_runs={list(ctx.last_agent_runs.keys())} "
                f"active_intent={ctx.active_intent} awaiting_save={ctx.awaiting_save}"
            )

            # ---- 2. Intent classification (always the first module) ---------
            ic_out = await self._run_module(
                AIModule.INTENT_CLASSIFIER, intent_classifier_service.run,
                turn, ctx, outputs, flow,
            )
            outputs[AIModule.INTENT_CLASSIFIER.value] = ic_out
            intent = ic_out.payload
            flow.append(f"identified intent: {intent.name}")
            trace_line(
                f"intent classifier: {intent.name} "
                f"(confidence={intent.confidence:.2f}, reasoning={intent.reasoning!r})"
            )

            # ---- 3. Classifier-only intents: surface the canned message -----
            if intent.name in _CLASSIFIER_ONLY_INTENTS and intent.raw is not None:
                canned = getattr(intent.raw, "out_of_scope_message", None)
                if canned:
                    return await self._finalize(
                        text=canned, intent=intent,
                        flow=flow, t0=t_all, db=db, uid=uid, sid=sid,
                    )

            # ---- 4. Pick the sequence ---------------------------------------
            sequence = self._sequence_for(intent, ctx)
            flow.append(f"sequence: {[m.value for m in sequence]}")
            trace_line(f"sequence: {[m.value for m in sequence]}")

            # ---- 5. Run each module in order, threading prior outputs -------
            for module in sequence:
                runner = self._modules[module]
                try:
                    outputs[module.value] = await asyncio.wait_for(
                        self._run_module(module, runner, turn, ctx, outputs, flow),
                        timeout=_PER_MODULE_TIMEOUT_S,
                    )
                except asyncio.TimeoutError:
                    logger.warning(
                        "Module %r timed out after %.0fs (session=%s); returning fallback",
                        module.value, _PER_MODULE_TIMEOUT_S, sid,
                    )
                    flow.append(f"{module.value} timed out — returning fallback")
                    return await self._finalize(
                        text=_INTENT_TIMEOUT_MESSAGE, intent=intent,
                        flow=flow, t0=t_all, db=db, uid=uid, sid=sid,
                    )

            # ---- 6. The last module owns the reply --------------------------
            final = outputs[sequence[-1].value]
            return await self._finalize(
                text=final.text or "", intent=intent,
                flow=flow, t0=t_all, db=db, uid=uid, sid=sid,
                final=final,
            )

        except Exception as exc:
            if _is_llm_auth_failure(exc):
                logger.warning(
                    "ChatBrain session=%s: LLM authentication failed (%s); using recovery path",
                    sid, exc,
                )
            else:
                logger.exception("ChatBrain turn failed session=%s: %s", sid, exc)
            flow.append(f"classifier or routing error: {exc!s}")
            trace_line(f"ChatBrain exception before recovery: {exc!s}")
            if db is not None:
                try:
                    await db.rollback()
                except Exception:
                    logger.exception(
                        "ChatBrain failed to rollback aborted transaction session=%s", sid,
                    )
            return await self._finalize(
                text=_CLASSIFIER_FAILURE_MESSAGE, intent=intent,
                flow=flow, t0=t_all, db=db, uid=uid, sid=sid,
            )

    # ---------------------------------------------------------------------
    # internals
    # ---------------------------------------------------------------------

    def _sequence_for(self, intent: IntentDecision, ctx: TurnContext) -> list[AIModule]:
        """Pick the module sequence for this turn.

        - ``ctx.awaiting_save`` (the goal-planning save gate) wins over the
          classifier and routes back to cashflow.
        - Otherwise look up ``intent.name`` in ``_SEQUENCES``.
        - Unknown intents fall through to general_chat.
        """
        if ctx.awaiting_save:
            return _AWAITING_SAVE_SEQUENCE
        return _SEQUENCES.get(intent.name, [AIModule.GENERAL_CHAT])

    async def _run_module(
        self,
        module: AIModule,
        runner: ModuleRunner,
        turn: ChatTurnInput,
        ctx: TurnContext,
        prior: dict[str, ModuleOutput],
        flow: list[str],
    ) -> ModuleOutput:
        """Invoke a module service. Centralised so logging stays uniform."""
        flow.append(f"→ {module.value}")
        return await runner(turn, ctx, prior)

    async def _finalize(
        self,
        *,
        text: str,
        intent: IntentDecision | None,
        flow: list[str],
        t0: float,
        db,
        uid,
        sid,
        final: ModuleOutput | None = None,
    ) -> ChatBrainResult:
        """Shape the assistant reply + write end-of-turn telemetry."""
        ms = int((time.perf_counter() - t0) * 1000)
        trace_response_preview(
            "final assistant message sent to client", text, max_chars=1200,
        )
        try:
            await asyncio.wait_for(
                log_chat_turn_flow_summary(
                    db,
                    user_id=uid,
                    session_id=sid,
                    intent=intent.name if intent else None,
                    intent_confidence=intent.confidence if intent else None,
                    steps=flow,
                    duration_ms=ms,
                ),
                timeout=_TELEMETRY_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "Chat turn telemetry timed out after %.1fs (session=%s); "
                "returning reply without telemetry row", _TELEMETRY_TIMEOUT_S, sid,
            )
        except Exception:
            logger.exception(
                "Chat turn telemetry failed (session=%s); returning reply anyway", sid,
            )
        return ChatBrainResult(
            content=text,
            intent=intent.name if intent else None,
            intent_confidence=intent.confidence if intent else None,
            intent_reasoning=intent.reasoning if intent else None,
            asset_allocation_run_id=final.persisted_run_id if final else None,
            ideal_allocation_rebalancing_id=final.rebalancing_recommendation_id if final else None,
            ideal_allocation_snapshot_id=final.snapshot_id if final else None,
            chart_payloads=final.chart_payloads if final else None,
        )
