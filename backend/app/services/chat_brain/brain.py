"""
Chat brain — single entrypoint for one user question.

Flow:
  1. Ingest turn (question + conversation context + preloaded user ORM graph).
  2. Classify intent (intent classifier module).
  3. Dispatch by intent: fetch/use only the data each branch needs (user_ctx already
     carries profile, risk, goals, portfolios via get_ai_user_context).
  4. Call specialist modules (market commentary, allocation spine, DB portfolio summary, general chat).
  5. Log flow summary and return tailored text + intent metadata.

The live HTTP chat router imports ``services.chat_core.ChatBrain`` instead; treat this file
as a mirror or experiment — keep behavior in sync when changing portfolio or allocation flow.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass

from app.services.ai_module_telemetry import log_chat_turn_flow_summary
from app.services.ai_bridge import (
    classify_user_message,
    generate_general_chat_response,
    generate_market_commentary,
    generate_portfolio_query_response,
)
from app.services.ai_bridge.ailax_flow import SpineMode, build_ailax_spine, detect_spine_mode
from app.services.ai_bridge.liquidity_gate import (
    assess_liquidity_for_cash_out,
    format_quick_cash_out_response,
)
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
            # --- Step 1–2: intent from question + recent turns ---
            classification = await classify_user_message(
                customer_question=turn.user_question,
                conversation_history=turn.conversation_history,
            )
            intent_value = classification.intent.value
            intent_confidence = classification.confidence
            intent_reasoning = classification.reasoning
            flow.append(f"identified intent: {intent_value}")

            # --- Step 3–5: dispatch (user data = turn.user_ctx, already loaded for AI) ---
            if intent_value == "general_market_query":
                return await finalize(
                    await self._answer_general_market(turn, classification, flow)
                )

            if intent_value in ("portfolio_optimisation", "goal_planning"):
                txt, rid, sid_alloc = await self._answer_portfolio_style(turn, flow)
                return await finalize(
                    txt,
                    ideal_allocation_rebalancing_id=rid,
                    ideal_allocation_snapshot_id=sid_alloc,
                )

            if intent_value == "portfolio_query":
                flow.append(
                    "portfolio snapshot intent → answered from DB holdings (no allocation engine)"
                )
                # user_ctx must include portfolios (loaded by get_ai_user_context)
                content = generate_portfolio_query_response(
                    user=turn.user_ctx,
                    user_question=turn.user_question,
                )
                return await finalize(content)

            flow.append("general chat (no specialist branch)")
            content = await generate_general_chat_response(
                user_question=turn.user_question,
                classification=classification,
                conversation_history=turn.conversation_history,
                client_context=turn.client_context,
            )
            return await finalize(content)

        except Exception as exc:
            logger.exception("ChatBrain turn failed session=%s: %s", sid, exc)
            flow.append(f"classifier or routing error: {exc!s}")
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
        try:
            return await generate_general_chat_response(
                user_question=turn.user_question,
                classification=classification,
                market_commentary=market_doc,
                conversation_history=turn.conversation_history,
                client_context=turn.client_context,
            )
        except Exception:
            logger.exception("General chat failed for session %s", turn.session_id)
            return await generate_general_chat_response(
                user_question=turn.user_question,
                classification=classification,
                conversation_history=turn.conversation_history,
                client_context=turn.client_context,
            )

    async def _answer_portfolio_style(
        self, turn: ChatTurnInput, flow: list[str]
    ) -> tuple[str, uuid.UUID | None, uuid.UUID | None]:
        """
        Uses turn.user_ctx (profile, risk_profile, investment_profile, goals, portfolios)
        inside allocation / liquidity helpers — no extra DB fetch here.
        """
        mode = detect_spine_mode(turn.user_question)
        flow.append(f"portfolio-style question → style={mode.value}")

        if mode == SpineMode.CASH_OUT:
            flow.append("liquidity check on saved emergency fund vs inferred need")
            gate = assess_liquidity_for_cash_out(turn.user_ctx, turn.user_question)
            if gate.sufficient_for_quick_cash_out_path:
                flow.append("liquidity OK → short cash-out reply only (no allocation engine)")
                return format_quick_cash_out_response(turn.user_ctx, turn.user_question, gate), None, None
            flow.append("liquidity not enough for quick path → running full allocation engine")

        flow.append("using client profile from DB (age, risk, goals, current mix)")
        flow.append(
            "ran Ideal_asset_allocation (5-step LCEL) via asset_allocation_service.compute_allocation_result"
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
        return (
            spine.text,
            spine.rebalancing_recommendation_id,
            spine.portfolio_allocation_snapshot_id,
        )

    async def _answer_after_classifier_failure(
        self, turn: ChatTurnInput, flow: list[str]
    ) -> _ClassifierFailureOutcome:
        if _looks_like_portfolio_optimisation(turn.user_question):
            try:
                mode = detect_spine_mode(turn.user_question)
                flow.append("keyword fallback → running allocation engine")
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
