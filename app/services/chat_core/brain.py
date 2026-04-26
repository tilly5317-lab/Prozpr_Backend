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
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chat_ai_module_run import ChatAiModuleRun
from app.services.ai_bridge.common import ensure_ai_agents_path
from app.services.ai_module_telemetry import log_chat_turn_flow_summary
from app.services.ai_bridge import (
    classify_user_message,
    generate_follow_up_response,
    generate_general_chat_response,
    generate_market_commentary,
    generate_portfolio_query_response,
)
from app.services.ai_bridge.ailax_flow import SpineMode, build_prozpr_spine, detect_spine_mode
from app.services.ai_bridge.ailax_trace import trace_line, trace_response_preview
from app.services.ai_bridge.liquidity_gate import (
    assess_liquidity_for_cash_out,
    format_quick_cash_out_response,
)
from app.services.ai_bridge.chart_selector_service import select_charts
from app.services.chat_core.types import ChatBrainResult, ChatTurnInput
from app.services.visualization_tools.registry import CHART_TOOLS

ensure_ai_agents_path()

from intent_classifier import FollowUpType, Intent

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


async def _load_last_session_intent(
    db: AsyncSession | None, session_id: uuid.UUID | None
) -> Intent | None:
    """Return the intent of the most recent chat turn for this session, if any.

    Used to seed ``active_intent`` on the classifier so follow-ups can stay on
    the prior topic (and so meta follow-ups resolve to the originating intent).
    """
    if db is None or session_id is None:
        return None
    stmt = (
        select(ChatAiModuleRun.intent_detected)
        .where(
            ChatAiModuleRun.session_id == session_id,
            ChatAiModuleRun.module == "chat_flow",
            ChatAiModuleRun.intent_detected.is_not(None),
        )
        .order_by(ChatAiModuleRun.created_at.desc())
        .limit(1)
    )
    row = (await db.execute(stmt)).scalar_one_or_none()
    if not row:
        return None
    try:
        return Intent(row)
    except ValueError:
        return None


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
        chart_select_task: asyncio.Task[list[str]] | None = None

        async def _resolve_chart_payloads() -> list[dict] | None:
            if chart_select_task is None or db is None:
                return None
            try:
                names = await chart_select_task
            except Exception as exc:
                logger.warning("chart selector task failed: %s", exc)
                return None
            payloads: list[dict] = []
            for name in names:
                tool = CHART_TOOLS.get(name)
                if tool is None:
                    continue
                try:
                    payload = await tool.builder(db, uid)
                except Exception:
                    logger.exception("chart builder failed for %s", name)
                    continue
                if payload is not None:
                    payloads.append(payload.model_dump())
            if payloads:
                flow.append(f"attached charts: {', '.join(p['type'] for p in payloads)}")
            return payloads or None

        async def finalize(
            content: str,
            *,
            ideal_allocation_rebalancing_id: uuid.UUID | None = None,
            ideal_allocation_snapshot_id: uuid.UUID | None = None,
        ) -> ChatBrainResult:
            chart_payloads = await _resolve_chart_payloads()
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
                chart_payloads=chart_payloads,
            )

        try:
            trace_line("--- ChatBrain.run_turn ---")
            trace_line(f"user message: {turn.user_question}")
            # --- Step 1–2: intent from question + recent turns (seed active_intent from the prior turn) ---
            active_intent = await _load_last_session_intent(db, sid)
            if active_intent is not None:
                trace_line(f"active_intent from prior turn: {active_intent.value}")
            classification = await classify_user_message(
                customer_question=turn.user_question,
                conversation_history=turn.conversation_history,
                active_intent=active_intent,
            )
            intent_value = classification.intent.value
            intent_confidence = classification.confidence
            intent_reasoning = classification.reasoning
            flow.append(f"identified intent: {intent_value}")
            # Kick off chart selection in parallel with text generation; awaited in finalize.
            chart_select_task = asyncio.create_task(
                select_charts(turn.user_question, intent_value)
            )
            trace_line(
                f"intent classifier: {intent_value} "
                f"(confidence={intent_confidence:.2f}, reasoning={intent_reasoning!r}, "
                f"is_follow_up={classification.is_follow_up}, "
                f"follow_up_type={classification.follow_up_type.value if classification.follow_up_type else None})"
            )

            # --- Meta follow-up short-circuit: question is about a prior assistant turn. ---
            # Answer from conversation history only; skip the specialist pipelines so we
            # don't re-run the allocation engine or a web search just to re-explain.
            # prior_intent is the classifier's resolved topic — follow_up_service uses it
            # to pick topic-specific guidance on top of the universal prompt base.
            if classification.follow_up_type == FollowUpType.META:
                flow.append(f"meta follow-up ({intent_value}) → answering from conversation history")
                trace_line(
                    f"next module: follow_up_service (meta follow-up, topic={intent_value})"
                )
                content = await generate_follow_up_response(
                    user_question=turn.user_question,
                    conversation_history=turn.conversation_history,
                    prior_intent=classification.intent,
                )
                trace_response_preview("follow_up_service response", content)
                return await finalize(content)

            # --- Step 3–5: dispatch (user data = turn.user_ctx, already loaded for AI) ---
            if intent_value == "general_market_query":
                trace_line("next module: general_market_query → market_commentary + general_chat")
                return await finalize(
                    await self._answer_general_market(turn, classification, flow)
                )

            if intent_value in ("portfolio_optimisation", "goal_planning"):
                trace_line(
                    "next module: portfolio-style spine → "
                    "ailax_flow.detect_spine_mode / liquidity_gate / goal_based_allocation_pydantic"
                )
                p_content, p_reb, p_snap = await self._answer_portfolio_style(turn, flow)
                return await finalize(
                    p_content,
                    ideal_allocation_rebalancing_id=p_reb,
                    ideal_allocation_snapshot_id=p_snap,
                )

            if intent_value == "portfolio_query":
                trace_line("next module: portfolio_query → app.services.ai_bridge.portfolio_query_service")
                flow.append(
                    "portfolio snapshot intent → answered from DB holdings (no allocation engine)"
                )
                # user_ctx must include portfolios (loaded by get_ai_user_context)
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

    async def _answer_portfolio_style(
        self, turn: ChatTurnInput, flow: list[str]
    ) -> tuple[str, uuid.UUID | None, uuid.UUID | None]:
        """
        Uses turn.user_ctx (profile, risk_profile, investment_profile, goals, portfolios)
        inside allocation / liquidity helpers — no extra DB fetch here.
        """
        mode = detect_spine_mode(turn.user_question)
        flow.append(f"portfolio-style question → style={mode.value}")
        trace_line(f"ailax_flow.detect_spine_mode → {mode.value}")

        if mode == SpineMode.CASH_OUT:
            flow.append("liquidity check on saved emergency fund vs inferred need")
            trace_line("module: liquidity_gate.assess_liquidity_for_cash_out")
            gate = assess_liquidity_for_cash_out(turn.user_ctx, turn.user_question)
            if gate.sufficient_for_quick_cash_out_path:
                flow.append("liquidity OK → short cash-out reply only (no allocation engine)")
                quick = format_quick_cash_out_response(turn.user_ctx, turn.user_question, gate)
                trace_response_preview("liquidity_gate quick cash-out response", quick)
                return quick, None, None
            flow.append("liquidity not enough for quick path → running full allocation engine")

        flow.append("using client profile from DB (age, risk, goals, current mix)")
        flow.append(
            "ran goal_based_allocation_pydantic (7-step pipeline) via asset_allocation_service.compute_allocation_result"
        )
        trace_line(
            "module chain: app.services.ai_bridge.ailax_flow.build_prozpr_spine "
            "→ asset_allocation_service.compute_allocation_result "
            "→ goal_based_allocation_pydantic.pipeline.run_allocation_with_state"
        )
        spine = await build_prozpr_spine(
            turn.user_ctx,
            turn.user_question,
            mode,
            db=turn.db,
            persist_recommendation=turn.db is not None,
            acting_user_id=turn.effective_user_id,
            chat_session_id=turn.session_id,
        )
        trace_response_preview("ailax_flow.build_prozpr_spine (chat brief)", spine.text)
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
                trace_line(
                    f"classifier failure recovery → keyword fallback → build_prozpr_spine (mode={mode.value})"
                )
                spine = await build_prozpr_spine(
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
