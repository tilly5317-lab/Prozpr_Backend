"""Chat core — `brain.py`.

Orchestrates a single user turn: intent classification, branch routing (market, portfolio query, portfolio-style spine with liquidity gate and allocation), optional telemetry, and assistant text. Depends on ``services.ai_bridge`` and preloaded ORM user context from ``get_ai_user_context``.
"""


from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass

import httpx

from app.services.ai_module_telemetry import log_chat_turn_flow_summary
from app.services.ai_bridge import (
    classify_user_message,
    generate_general_chat_response,
    generate_market_commentary,
    generate_portfolio_query_response,
)
from app.services.ai_bridge.ailax_flow import build_ailax_spine, detect_spine_mode
from app.services.ai_bridge.ailax_trace import trace_line, trace_response_preview
from app.services.chat_core.turn_context import build_turn_context, TurnContext
from app.services.chat_core.types import ChatBrainResult, ChatTurnInput

logger = logging.getLogger(__name__)

_PORTFOLIO_OPTIM_FALLBACK_TRIGGERS = (
    "portfolio",
    "allocation",
    "rebalance",
    "rebalanc",
    "review my portfolio",
    "optimi",
    "asset mix",
)


def _looks_like_portfolio_optimisation(text: str) -> bool:
    t = text.lower()
    return any(tok in t for tok in _PORTFOLIO_OPTIM_FALLBACK_TRIGGERS)


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


@dataclass
class _ClassifierFailureOutcome:
    content: str
    intent: str | None = None
    intent_confidence: float | None = None
    intent_reasoning: str | None = None
    ideal_allocation_rebalancing_id: uuid.UUID | None = None
    ideal_allocation_snapshot_id: uuid.UUID | None = None


class ChatBrain:
    """
    Orchestrates one chat turn. Stateless: safe to instantiate per request.
    """

    async def run_turn(self, turn: ChatTurnInput) -> ChatBrainResult:
        uid = turn.effective_user_id
        sid = turn.session_id
        db = turn.db
        flow: list[str] = []
        t_all = time.perf_counter()

        intent_value: str | None = None
        intent_confidence: float | None = None
        intent_reasoning: str | None = None

        async def finalize(
            content: str,
            *,
            ideal_allocation_rebalancing_id: uuid.UUID | None = None,
            ideal_allocation_snapshot_id: uuid.UUID | None = None,
        ) -> ChatBrainResult:
            ms = int((time.perf_counter() - t_all) * 1000)
            trace_line(f"file: app/services/chat_core/brain.py → finalize (session={sid})")
            trace_response_preview("final assistant message sent to client", content, max_chars=1200)
            await log_chat_turn_flow_summary(
                db,
                user_id=uid,
                session_id=sid,
                intent=intent_value,
                steps=flow,
                duration_ms=ms,
            )
            return ChatBrainResult(
                content=content,
                intent=intent_value,
                intent_confidence=intent_confidence,
                intent_reasoning=intent_reasoning,
                ideal_allocation_rebalancing_id=ideal_allocation_rebalancing_id,
                ideal_allocation_snapshot_id=ideal_allocation_snapshot_id,
            )

        try:
            trace_line("--- ChatBrain.run_turn ---")
            trace_line(f"user message: {turn.user_question}")
            # --- Step 0: per-turn context bundle (history + last AgentRun per module) ---
            turn_context: TurnContext = await build_turn_context(turn)
            trace_line(
                f"turn_context: last_runs={list(turn_context.last_agent_runs.keys())} "
                f"active_intent={turn_context.active_intent}"
            )
            # --- Step 1–2: intent from question + recent turns ---
            classification = await classify_user_message(
                customer_question=turn.user_question,
                conversation_history=turn.conversation_history,
                active_intent=turn_context.active_intent,
            )
            intent_value = classification.intent.value
            intent_confidence = classification.confidence
            intent_reasoning = classification.reasoning
            flow.append(f"identified intent: {intent_value}")
            trace_line(
                f"intent classifier: {intent_value} "
                f"(confidence={intent_confidence:.2f}, reasoning={intent_reasoning!r})"
            )

            # --- Step 3–5: dispatch (user data = turn.user_ctx, already loaded for AI) ---
            if intent_value == "general_market_query":
                trace_line("next module: general_market_query → market_commentary + general_chat")
                return await finalize(
                    await self._answer_general_market(turn, classification, flow)
                )

            if intent_value in ("portfolio_optimisation", "goal_planning"):
                # Local imports — chat handler self-registers via @register at import time.
                from app.services.ai_bridge import asset_allocation_chat  # noqa: F401
                from app.services.ai_bridge.chat_dispatcher import dispatch_chat
                flow.append("dispatch_chat → asset_allocation_chat")
                trace_line("next module: chat_dispatcher → asset_allocation_chat")
                result = await dispatch_chat(intent_value, turn_context)
                return await finalize(
                    result.text,
                    ideal_allocation_snapshot_id=result.snapshot_id,
                    ideal_allocation_rebalancing_id=result.rebalancing_recommendation_id,
                )

            if intent_value == "portfolio_query":
                trace_line("next module: portfolio_query → app.services.ai_bridge.portfolio_query_service")
                flow.append(
                    "portfolio_query → AI_Agents.portfolio_query orchestrator (market commentary + sub-category roll-ups)"
                )
                # user_ctx must include portfolios + holdings → fund_metadata (loaded by get_ai_user_context)
                content = await generate_portfolio_query_response(
                    user=turn.user_ctx,
                    user_question=turn.user_question,
                    conversation_history=turn.conversation_history,
                )
                trace_response_preview("portfolio_query_service response", content)
                return await finalize(content)

            flow.append("general chat (no specialist branch)")
            trace_line("next module: general_chat (no specialist branch)")
            content = await generate_general_chat_response(
                user_question=turn.user_question,
                classification=classification,
                conversation_history=turn.conversation_history,
                client_context=turn.client_context,
            )
            trace_response_preview("general_chat_service response", content)
            return await finalize(content)

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
            recovery = await self._answer_after_classifier_failure(turn, flow)
            if recovery.intent is not None:
                intent_value = recovery.intent
                intent_confidence = recovery.intent_confidence
                intent_reasoning = recovery.intent_reasoning
            return await finalize(
                recovery.content,
                ideal_allocation_rebalancing_id=recovery.ideal_allocation_rebalancing_id,
                ideal_allocation_snapshot_id=recovery.ideal_allocation_snapshot_id,
            )

    async def _answer_general_market(self, turn: ChatTurnInput, classification, flow: list[str]) -> str:
        flow.append("running market commentary module (macro context)")
        trace_line("module: market_commentary_service (generate_market_commentary)")
        market_doc: str | None = None
        try:
            market_doc = await asyncio.wait_for(
                generate_market_commentary(
                    user_question=turn.user_question,
                    conversation_history=turn.conversation_history,
                ),
                timeout=120.0,
            )
            flow.append("market commentary completed")
            trace_response_preview("market_commentary_service response", market_doc or "")
        except asyncio.TimeoutError:
            logger.warning(
                "Market commentary timed out (120s) for session %s; continuing without macro doc",
                turn.session_id,
            )
            flow.append("market commentary timed out — skipped macro doc")
        except Exception:
            logger.exception("Market commentary failed for session %s", turn.session_id)
            flow.append("market commentary failed — see logs")

        flow.append("tailoring final reply (general chat module)")
        trace_line("module: general_chat_service (with optional market_doc)")
        try:
            reply = await generate_general_chat_response(
                user_question=turn.user_question,
                classification=classification,
                market_commentary=market_doc,
                conversation_history=turn.conversation_history,
                client_context=turn.client_context,
            )
            trace_response_preview("general_chat_service response", reply)
            return reply
        except Exception:
            logger.exception("General chat failed for session %s", turn.session_id)
            reply = await generate_general_chat_response(
                user_question=turn.user_question,
                classification=classification,
                conversation_history=turn.conversation_history,
                client_context=turn.client_context,
            )
            trace_response_preview("general_chat_service response (fallback)", reply)
            return reply

    async def _answer_after_classifier_failure(
        self, turn: ChatTurnInput, flow: list[str]
    ) -> _ClassifierFailureOutcome:
        if _looks_like_portfolio_optimisation(turn.user_question):
            try:
                mode = detect_spine_mode(turn.user_question)
                flow.append("keyword fallback → running allocation engine")
                trace_line(
                    f"classifier failure recovery → keyword fallback → build_ailax_spine (mode={mode.value})"
                )
                spine = await build_ailax_spine(
                    turn.user_ctx,
                    turn.user_question,
                    mode,
                    db=turn.db,
                    persist_recommendation=turn.db is not None,
                    acting_user_id=turn.effective_user_id,
                    chat_session_id=turn.session_id,
                )
                if spine.text:
                    return _ClassifierFailureOutcome(
                        content=spine.text,
                        intent="portfolio_optimisation",
                        intent_confidence=0.5,
                        intent_reasoning="Keyword fallback route used due classifier failure.",
                        ideal_allocation_rebalancing_id=spine.rebalancing_recommendation_id,
                        ideal_allocation_snapshot_id=spine.portfolio_allocation_snapshot_id,
                    )
            except Exception:
                logger.exception("Portfolio fallback failed for session %s", turn.session_id)
            return _ClassifierFailureOutcome(
                content=(
                    "I can review your portfolio, but the optimisation engine is temporarily unavailable.\n\n"
                    "**Justification**\n"
                    "- The classifier failed and fallback optimisation also failed in this request."
                ),
            )
        return _ClassifierFailureOutcome(
            content=(
                "I can help with that, but there was a temporary processing issue.\n\n"
                "**Justification**\n"
                "- Intent classification is currently unavailable, so I returned a safe fallback response."
            ),
        )
