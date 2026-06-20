"""``ChatBrain`` — orchestrates one chat turn.

The flow (matches the chat-flow design doc):

    1. build_turn_context(turn)             per-turn bag of state
    2. classify_intent                       →  IntentDecision
    3. FLOWS[intent.name]                     →  one explicit flow (recipe)
    4. await flow(turn, ctx)                  →  runs the domain functions in
                                                 order and returns the final
                                                 ModuleOutput (reply text + ids)
    5. finalize + telemetry → ChatBrainResult

Architectural rule: the brain owns ONLY the envelope (intent → flow lookup,
timeout, error recovery, telemetry). Each flow lives in ``flows.py`` and is the
ONLY place domains are composed; a domain function never calls another domain.

Adding a new intent is two edits, both in ``flows.py``: a new ``flow_*``
function + one row in ``FLOWS``. The brain never changes.
"""

from __future__ import annotations

import asyncio
import logging
import time

import httpx

from app.domains.ai_engine.common import trace_line, trace_response_preview
from app.domains.ai_engine.services.flow import (
    FLOWS,
    AWAITING_SAVE_FLOW,
    flow_general_chat,
)
from app.domains.ai_engine.turn_context import (
    TurnContext,
    build_turn_context,
)
from app.domains.ai_engine.chat_types import (
    ChatBrainResult,
    ChatTurnInput,
)
from app.domains.ai_engine.types import IntentDecision, ModuleOutput
from app.domains.chat.services.ai_module_telemetry import log_chat_turn_flow_summary
from app.domains.intent_classifier.services import intent_classifier_service

logger = logging.getLogger(__name__)

_CLASSIFIER_FAILURE_MESSAGE = "Sorry — I couldn't process your request due to a technical issue. Please try again."
_INTENT_TIMEOUT_MESSAGE = (
    "That took longer than expected on my end — please try again in a moment."
)

# Best-effort caps so a slow downstream never keeps the user waiting forever.
_TELEMETRY_TIMEOUT_S = 5.0
# A flow may run several domain steps in sequence, so it gets a wider cap than
# a single module did.
_FLOW_TIMEOUT_S = 180.0

# Sentinel for the legacy goal-planning canned redirect. The classifier still
# uses it to strip pre-cutover refusals out of historical chat. Kept here so
# the name stays importable from ``app.domains.ai_engine``.
_GOAL_PLANNING_SENTINEL = "isn't built into the chat yet"


# Intents whose classifier output is itself the reply (canned out-of-scope /
# stock-advice redirects). No flow runs.
_CLASSIFIER_ONLY_INTENTS: frozenset[str] = frozenset(
    {
        "out_of_scope",
        "stock_advice",
    }
)


def _is_llm_auth_failure(exc: BaseException) -> bool:
    """Anthropic/OpenAI rejected credentials — expected until .env keys are valid."""
    if isinstance(exc, httpx.HTTPStatusError) and exc.response is not None:
        if exc.response.status_code == 401:
            return True
    msg = str(exc).lower()
    return (
        "401" in msg
        and (
            "unauthorized" in msg
            or "invalid x-api-key" in msg
            or "authentication_error" in msg
        )
    ) or ("invalid x-api-key" in msg)


# ---------------------------------------------------------------------------
# ChatBrain
# ---------------------------------------------------------------------------


class ChatBrain:
    """Orchestrates one chat turn. Stateless: safe to instantiate per request."""

    async def run_turn(self, turn: ChatTurnInput) -> ChatBrainResult:
        sid = turn.session_id
        uid = turn.effective_user_id
        db = turn.db
        flow: list[str] = []
        t_all = time.perf_counter()

        intent: IntentDecision | None = None

        try:
            trace_line("--- ChatBrain.run_turn ---")
            trace_line(f"user message: {turn.user_question}")

            # ---- 1. Per-turn context ----------------------------------------
            ctx: TurnContext = await build_turn_context(turn)
            trace_line(
                f"turn_context: last_runs={list(ctx.last_agent_runs.keys())} "
                f"active_intent={ctx.active_intent} awaiting_save={ctx.awaiting_save}"
            )

            # ---- 2. Intent classification (always first) --------------------
            ic_out = await intent_classifier_service.run(turn, ctx, {})
            intent = ic_out.payload
            flow.append(f"identified intent: {intent.name}")
            trace_line(
                f"intent classifier: {intent.name} "
                f"(confidence={intent.confidence:.2f}, reasoning={intent.reasoning!r})"
            )

            # ---- 3. Classifier-only intents: tailor the redirect, else canned -
            if intent.name in _CLASSIFIER_ONLY_INTENTS and intent.raw is not None:
                canned = getattr(intent.raw, "out_of_scope_message", None)
                if canned:
                    # Function-local import keeps the brain free of module-level
                    # domain deps (its convention) and avoids any import cycle.
                    from app.domains.general_chat.services.general_chat_engine import (
                        format_redirect_or_canned,
                    )

                    text = await format_redirect_or_canned(ctx=ctx, intent=intent)
                    return await self._finalize(
                        text=text,
                        intent=intent,
                        flow=flow,
                        t0=t_all,
                        db=db,
                        uid=uid,
                        sid=sid,
                    )

            # ---- 4. Pick the flow -------------------------------------------
            selected = self._flow_for(intent, ctx)
            flow.append(f"flow: {selected.__name__}")
            trace_line(f"flow: {selected.__name__}")

            # ---- 5. Run the flow (it composes the domain steps itself) ------
            try:
                final: ModuleOutput = await asyncio.wait_for(
                    selected(turn, ctx),
                    timeout=_FLOW_TIMEOUT_S,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "Flow %r timed out after %.0fs (session=%s); returning fallback",
                    selected.__name__,
                    _FLOW_TIMEOUT_S,
                    sid,
                )
                flow.append(f"{selected.__name__} timed out — returning fallback")
                # The cancelled flow may have been mid-statement on the shared
                # session, leaving its transaction invalidated (pending rollback).
                # Roll back — exactly as the general except-handler below does —
                # so _finalize's telemetry write and the router's final commit
                # don't fail with PendingRollbackError.
                if db is not None:
                    try:
                        await db.rollback()
                    except Exception:
                        logger.exception(
                            "ChatBrain failed to rollback after flow timeout session=%s",
                            sid,
                        )
                return await self._finalize(
                    text=_INTENT_TIMEOUT_MESSAGE,
                    intent=intent,
                    flow=flow,
                    t0=t_all,
                    db=db,
                    uid=uid,
                    sid=sid,
                )

            # ---- 6. The flow's result owns the reply ------------------------
            return await self._finalize(
                text=final.text or "",
                intent=intent,
                flow=flow,
                t0=t_all,
                db=db,
                uid=uid,
                sid=sid,
                final=final,
            )

        except Exception as exc:
            if _is_llm_auth_failure(exc):
                logger.warning(
                    "ChatBrain session=%s: LLM authentication failed (%s); using recovery path",
                    sid,
                    exc,
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
                        "ChatBrain failed to rollback aborted transaction session=%s",
                        sid,
                    )
            return await self._finalize(
                text=_CLASSIFIER_FAILURE_MESSAGE,
                intent=intent,
                flow=flow,
                t0=t_all,
                db=db,
                uid=uid,
                sid=sid,
            )

    # ---------------------------------------------------------------------
    # internals
    # ---------------------------------------------------------------------

    def _flow_for(self, intent: IntentDecision, ctx: TurnContext):
        """Pick the flow for this turn.

        - ``ctx.awaiting_save`` (the cashflow save gate) wins over the
          classifier and routes back to the goal-planning flow.
        - Otherwise look up ``intent.name`` in ``FLOWS``.
        - Unknown intents fall through to ``flow_general_chat``.
        """
        if ctx.awaiting_save:
            return AWAITING_SAVE_FLOW
        return FLOWS.get(intent.name, flow_general_chat)

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
            "final assistant message sent to client",
            text,
            max_chars=1200,
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
                "returning reply without telemetry row",
                _TELEMETRY_TIMEOUT_S,
                sid,
            )
        except Exception:
            logger.exception(
                "Chat turn telemetry failed (session=%s); returning reply anyway",
                sid,
            )
        return ChatBrainResult(
            content=text,
            intent=intent.name if intent else None,
            intent_confidence=intent.confidence if intent else None,
            intent_reasoning=intent.reasoning if intent else None,
            asset_allocation_run_id=final.persisted_run_id if final else None,
            ideal_allocation_rebalancing_id=final.rebalancing_recommendation_id
            if final
            else None,
            ideal_allocation_snapshot_id=final.snapshot_id if final else None,
            chart_payloads=final.chart_payloads if final else None,
        )
