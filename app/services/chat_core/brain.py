"""Chat core — `brain.py`.

Orchestrates a single user turn: intent classification, branch routing (market, portfolio query, portfolio-style spine with liquidity gate and allocation), optional telemetry, and assistant text. Depends on ``services.ai_bridge`` and preloaded ORM user context from ``get_ai_user_context``.
"""


from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any

import httpx
from sqlalchemy import select as sa_select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.services.ai_module_telemetry import log_chat_turn_flow_summary
from app.services.ai_bridge import (
    classify_user_message,
    generate_general_chat_response,
    generate_market_commentary,
    generate_portfolio_query_response,
)
from app.services.ai_bridge.common import trace_line, trace_response_preview
from app.services.chat_core.turn_context import build_turn_context, TurnContext
from app.services.chat_core.types import ChatBrainResult, ChatTurnInput

logger = logging.getLogger(__name__)

_CLASSIFIER_FAILURE_MESSAGE = (
    "I can help with that, but there was a temporary processing issue.\n\n"
    "**Justification**\n"
    "- Intent classification is currently unavailable, so I returned a safe fallback response."
)


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


def _enrich_client_context_with_first_name(
    ctx: dict[str, Any] | None, user_ctx: Any,
) -> dict[str, Any] | None:
    """Inject ``first_name`` from the User ORM into a shallow copy of ``ctx``.

    The frontend-supplied ``client_context`` does not include the customer's
    name today; the general-chat prompt's personalization rule needs it for
    occasional warm responses. Returns the original ``ctx`` unchanged when no
    first_name is available; never mutates the input dict.
    """
    first_name = getattr(user_ctx, "first_name", None) if user_ctx is not None else None
    if not first_name:
        return ctx
    enriched = dict(ctx) if ctx else {}
    enriched["first_name"] = first_name
    return enriched


async def _dump_aa_db_tables(db: AsyncSession | None, run_id: uuid.UUID | None) -> str:
    """[TEMPORARY] Query all asset_allocation_* tables for *run_id* and return markdown."""
    if db is None or run_id is None:
        return ""
    try:
        from app.models.asset_allocation.run import AssetAllocationRun, AssetAllocationRunTarget
        from app.models.asset_allocation.bucket import (
            AssetAllocationAggregate,
            AssetAllocationBucket,
            AssetAllocationBucketRunTarget,
        )

        def _f(v: Any) -> str:
            if v is None:
                return "-"
            if isinstance(v, float):
                return f"{v:,.2f}"
            return str(v)

        lines: list[str] = ["\n\n---\n## 🗄️ DB Tables Written (asset_allocation_*)\n"]

        # 1. asset_allocation_runs
        run = (await db.execute(
            sa_select(AssetAllocationRun).where(AssetAllocationRun.id == run_id)
        )).scalar_one_or_none()
        if run is None:
            return "\n\n---\n⚠️ No asset_allocation_runs row found for run_id=" + str(run_id)

        lines.append("### 1. `asset_allocation_runs`")
        lines.append("")
        lines.append("| Column | Value |")
        lines.append("|---|---|")
        lines.append(f"| **run_id** | `{run.id}` |")
        lines.append(f"| user_id | `{run.user_id}` |")
        lines.append(f"| portfolio_id | `{run.portfolio_id}` |")
        lines.append(f"| chat_session_id | `{run.chat_session_id}` |")
        lines.append(f"| status | {run.status.value} |")
        lines.append(f"| pipeline_source | {run.pipeline_source} |")
        lines.append(f"| spine_mode | {run.spine_mode} |")
        lines.append(f"| client_age | {run.client_age} |")
        lines.append(f"| client_risk_score | {_f(run.client_effective_risk_score)} |")
        lines.append(f"| total_corpus | ₹{_f(run.total_corpus)} |")
        lines.append(f"| grand_total | ₹{_f(run.grand_total)} |")
        lines.append(f"| equity | ₹{_f(run.equity_total)} ({_f(run.equity_total_pct)}%) |")
        lines.append(f"| debt | ₹{_f(run.debt_total)} ({_f(run.debt_total_pct)}%) |")
        lines.append(f"| others | ₹{_f(run.others_total)} ({_f(run.others_total_pct)}%) |")
        lines.append(f"| created_at | {run.created_at} |")
        lines.append("")

        # 2. asset_allocation_run_targets
        targets = (await db.execute(
            sa_select(AssetAllocationRunTarget).where(AssetAllocationRunTarget.run_id == run_id)
        )).scalars().all()
        lines.append(f"### 2. `asset_allocation_run_targets` ({len(targets)} rows)")
        lines.append("")
        if targets:
            lines.append("| target_id | goal_name | months | amount_needed | priority | investment_goal |")
            lines.append("|---|---|---|---|---|---|")
            for t in targets:
                lines.append(
                    f"| `{str(t.id)[:8]}…` | {t.goal_name} | {t.time_to_goal_months} "
                    f"| ₹{_f(t.amount_needed)} | {t.goal_priority} | {t.investment_goal} |"
                )
        else:
            lines.append("_(no rows)_")
        lines.append("")

        # 3. asset_allocation_buckets + children
        buckets = (await db.execute(
            sa_select(AssetAllocationBucket)
            .where(AssetAllocationBucket.run_id == run_id)
            .options(
                selectinload(AssetAllocationBucket.bucket_run_targets)
                    .selectinload(AssetAllocationBucketRunTarget.run_target),
                selectinload(AssetAllocationBucket.subgroups),
                selectinload(AssetAllocationBucket.asset_classes),
            )
        )).scalars().all()

        lines.append(f"### 3. `asset_allocation_buckets` ({len(buckets)} rows)")
        lines.append("")
        if buckets:
            lines.append("| bucket | goal_amount | allocated | future_inv | future_msg |")
            lines.append("|---|---|---|---|---|")
            for b in buckets:
                lines.append(
                    f"| **{b.bucket_name.value}** | ₹{_f(b.total_goal_amount)} "
                    f"| ₹{_f(b.allocated_amount)} | ₹{_f(b.future_investment_amount)} "
                    f"| {(b.future_investment_message or '-')[:60]} |"
                )
        lines.append("")

        # 4. bucket_run_targets (goals per bucket)
        total_links = sum(len(b.bucket_run_targets) for b in buckets)
        lines.append(f"### 4. `asset_allocation_bucket_run_targets` ({total_links} rows)")
        lines.append("")
        if total_links:
            lines.append("| bucket | goal_name | rationale |")
            lines.append("|---|---|---|")
            for b in buckets:
                for link in b.bucket_run_targets:
                    gname = link.run_target.goal_name if link.run_target else "?"
                    lines.append(
                        f"| {b.bucket_name.value} | {gname} "
                        f"| {(link.goal_rationale or '-')[:80]} |"
                    )
        else:
            lines.append("_(no rows)_")
        lines.append("")

        # 5. bucket_subgroups
        total_sgs = sum(len(b.subgroups) for b in buckets)
        lines.append(f"### 5. `asset_allocation_bucket_subgroups` ({total_sgs} rows)")
        lines.append("")
        if total_sgs:
            lines.append("| bucket | subgroup | planned | actual | planned_% | actual_% |")
            lines.append("|---|---|---|---|---|---|")
            for b in buckets:
                for sg in b.subgroups:
                    lines.append(
                        f"| {b.bucket_name.value} | {sg.subgroup} "
                        f"| ₹{_f(sg.planned_amount)} | ₹{_f(sg.actual_amount)} "
                        f"| {_f(sg.planned_pct_of_bucket)}% | {_f(sg.actual_pct_of_bucket)}% |"
                    )
        else:
            lines.append("_(no rows)_")
        lines.append("")

        # 6. bucket_asset_classes
        total_acs = sum(len(b.asset_classes) for b in buckets)
        lines.append(f"### 6. `asset_allocation_bucket_asset_classes` ({total_acs} rows)")
        lines.append("")
        if total_acs:
            lines.append("| bucket | kind | equity | debt | others | eq% | debt% | oth% |")
            lines.append("|---|---|---|---|---|---|---|---|")
            for b in buckets:
                for ac in b.asset_classes:
                    lines.append(
                        f"| {b.bucket_name.value} | {ac.split_kind.value} "
                        f"| ₹{_f(ac.equity_amount)} | ₹{_f(ac.debt_amount)} | ₹{_f(ac.others_amount)} "
                        f"| {_f(ac.equity_pct)} | {_f(ac.debt_pct)} | {_f(ac.others_pct)} |"
                    )
        else:
            lines.append("_(no rows)_")
        lines.append("")

        # 7. asset_allocation_aggregate
        aggs = (await db.execute(
            sa_select(AssetAllocationAggregate).where(AssetAllocationAggregate.run_id == run_id)
        )).scalars().all()
        lines.append(f"### 7. `asset_allocation_aggregate` ({len(aggs)} rows)")
        lines.append("")
        if aggs:
            lines.append("| kind | equity | debt | others | eq% | debt% | oth% |")
            lines.append("|---|---|---|---|---|---|---|")
            for a in aggs:
                lines.append(
                    f"| **{a.split_kind.value}** | ₹{_f(a.equity_amount)} "
                    f"| ₹{_f(a.debt_amount)} | ₹{_f(a.others_amount)} "
                    f"| {_f(a.equity_pct)} | {_f(a.debt_pct)} | {_f(a.others_pct)} |"
                )
        else:
            lines.append("_(no rows)_")
        lines.append("")

        return "\n".join(lines)
    except Exception as exc:
        logger.warning("_dump_aa_db_tables failed: %s", exc)
        return f"\n\n---\n⚠️ DB table dump failed: {exc}"


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
            asset_allocation_run_id: uuid.UUID | None = None,
            ideal_allocation_snapshot_id: uuid.UUID | None = None,
            chart_payloads: list[dict[str, Any]] | None = None,
        ) -> ChatBrainResult:
            ms = int((time.perf_counter() - t_all) * 1000)
            trace_line(f"file: app/services/chat_core/brain.py → finalize (session={sid})")
            trace_response_preview("final assistant message sent to client", content, max_chars=1200)
            await log_chat_turn_flow_summary(
                db,
                user_id=uid,
                session_id=sid,
                intent=intent_value,
                intent_confidence=intent_confidence,
                steps=flow,
                duration_ms=ms,
            )
            # [TEMPORARY] Append DB table dump when asset allocation ran
            if asset_allocation_run_id is not None:
                db_dump = await _dump_aa_db_tables(db, asset_allocation_run_id)
                if db_dump:
                    content = content + db_dump
            return ChatBrainResult(
                content=content,
                intent=intent_value,
                intent_confidence=intent_confidence,
                intent_reasoning=intent_reasoning,
                asset_allocation_run_id=asset_allocation_run_id,
                ideal_allocation_snapshot_id=ideal_allocation_snapshot_id,
                chart_payloads=chart_payloads,
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
            try:
                classification = await classify_user_message(
                    customer_question=turn.user_question,
                    conversation_history=turn.conversation_history,
                    active_intent=turn_context.active_intent,
                )
                intent_value = classification.intent.value
                intent_confidence = classification.confidence
                intent_reasoning = classification.reasoning
            except Exception as clf_exc:
                logger.warning(
                    "Intent classifier failed (%s); trying keyword fallback", clf_exc,
                )
                intent_value = self._keyword_fallback_intent(turn.user_question)
                intent_confidence = 0.5
                intent_reasoning = "keyword_fallback (classifier unavailable)"
                classification = None

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

            if intent_value == "asset_allocation":
                # Local import — chat handler self-registers via @register at import time.
                from app.services.ai_bridge.asset_allocation import chat as _aa_chat  # noqa: F401
                from app.services.ai_bridge.chat_dispatcher import dispatch_chat
                flow.append("dispatch_chat → asset_allocation_chat")
                trace_line("next module: chat_dispatcher → asset_allocation_chat")

                result = await dispatch_chat(intent_value, turn_context)

                # --- COMMENTED OUT: chart selection + builder for AA testing ---
                # Once asset allocation response is verified, uncomment this block
                # to re-enable chart selection and chart building.
                #
                # # Kick off chart selection in parallel with the formatter LLM.
                # selector_task = asyncio.create_task(
                #     select_charts(turn.user_question, intent_value)
                # )
                #
                # # Wait for the selector with a soft 3s ceiling — if it's still running
                # # because the formatter returned fast, cancel and ship without charts
                # # rather than block the response.
                # try:
                #     chart_names = await asyncio.wait_for(selector_task, timeout=3.0)
                # except asyncio.TimeoutError:
                #     logger.warning("AA chart selector timed out; shipping without charts")
                #     selector_task.cancel()
                #     chart_names = []
                # except Exception as exc:
                #     logger.warning("AA chart selector failed (%s); shipping without charts", exc)
                #     chart_names = []
                #
                # chart_payloads: list[dict[str, Any]] | None = None
                # if chart_names and db is not None:
                #     try:
                #         payloads = await build_charts_for_aa(db, uid, chart_names)
                #         if payloads:
                #             chart_payloads = [p.model_dump(mode="json") for p in payloads]
                #     except Exception:
                #         logger.exception("AA chart builder failed; shipping without charts")
                # --- END COMMENTED OUT ---

                return await finalize(
                    result.text,
                    ideal_allocation_snapshot_id=result.snapshot_id,
                    asset_allocation_run_id=result.asset_allocation_run_id,
                )

            if intent_value == "goal_planning":
                # No agent module yet — return the canned redirect attached
                # by the classifier. When the goal_planning module ships,
                # replace this branch with a dispatch_chat("goal_planning", ...) call.
                flow.append("goal_planning → canned redirect (module not yet built)")
                trace_line("next module: goal_planning → canned redirect")
                redirect_text = (
                    classification.out_of_scope_message
                    or "Goal planning isn't available yet — please ask me about your portfolio or where to invest."
                )
                return await finalize(redirect_text)

            if intent_value == "rebalancing":
                # Local import — chat handler self-registers via @register at import time.
                from app.services.ai_bridge.rebalancing import chat as _rb_chat  # noqa: F401
                from app.services.ai_bridge.chat_dispatcher import dispatch_chat
                flow.append("dispatch_chat → rebalancing_chat")
                trace_line("next module: chat_dispatcher → rebalancing_chat")

                # Dispatch the rebalancing chat handler (runs the engine + formatter
                # internally). After it returns, kick off chart selection, build
                # payloads from the engine response, and attach to the reply.
                #
                # Note: the formatter LLM lives inside dispatch_chat, so the selector
                # here cannot truly run parallel to it without a deeper refactor.
                # The Plan 2 win is removing the chart_picker LLM from the critical
                # path entirely (it used to run AFTER the formatter); the selector
                # is comparable in latency to the picker it replaces.
                result = await dispatch_chat(intent_value, turn_context)

                # --- COMMENTED OUT: chart selection for rebalancing testing ---
                # response = getattr(result, "rebalancing_response", None)
                # chart_payloads: list[dict[str, Any]] | None = None
                # if response is not None:
                #     selector_task = asyncio.create_task(
                #         select_charts(turn.user_question, intent_value)
                #     )
                #     try:
                #         chart_names = await asyncio.wait_for(selector_task, timeout=3.0)
                #     except asyncio.TimeoutError:
                #         logger.warning("Rebal chart selector timed out; shipping without charts")
                #         selector_task.cancel()
                #         chart_names = []
                #     except Exception as exc:
                #         logger.warning("Rebal chart selector failed (%s); shipping without charts", exc)
                #         chart_names = []
                #
                #     if chart_names:
                #         try:
                #             payloads = await build_charts_for_rebalancing(response, chart_names)
                #             if payloads:
                #                 chart_payloads = [p.model_dump(mode="json") for p in payloads]
                #         except Exception:
                #             logger.exception("Rebal chart builder failed; shipping without charts")
                # --- END COMMENTED OUT ---

                return await finalize(
                    result.text,
                    ideal_allocation_snapshot_id=result.snapshot_id,
                    asset_allocation_run_id=result.asset_allocation_run_id,
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
                client_context=_enrich_client_context_with_first_name(
                    turn.client_context, turn.user_ctx,
                ),
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
            if db is not None:
                try:
                    await db.rollback()
                except Exception:
                    logger.exception("ChatBrain failed to rollback aborted transaction session=%s", sid)
            return await finalize(_CLASSIFIER_FAILURE_MESSAGE)

    @staticmethod
    def _keyword_fallback_intent(question: str) -> str:
        """Simple keyword match when the LLM classifier is unavailable.

        Returns intents whose handlers can work without a ClassificationResult.
        Falls back to general chat for anything unrecognized.
        """
        import re
        q = question.lower()
        if re.search(r"\b(rebalanc|bring\s+back|align\s+portfolio|drift)\b", q):
            return "rebalancing"
        if re.search(r"\b(allocat|asset\s*alloc|ideal\s*alloc|where\s+should\s+i\s+invest|how\s+to\s+invest)\b", q):
            return "asset_allocation"
        if re.search(r"\b(portfolio|holding|fund|scheme|nav|return|xirr|cagr)\b", q):
            return "portfolio_query"
        if re.search(r"\b(market|nifty|sensex|index|economy|inflation|rbi|gdp|rate\s*cut)\b", q):
            return "general_market_query"
        if re.search(r"\b(goal|plan|retire|child|education|house|wedding|emergency)\b", q):
            return "goal_planning"
        return "general_chat"

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
                client_context=_enrich_client_context_with_first_name(
                    turn.client_context, turn.user_ctx,
                ),
            )
            trace_response_preview("general_chat_service response", reply)
            return reply
        except Exception:
            logger.exception("General chat failed for session %s", turn.session_id)
            reply = await generate_general_chat_response(
                user_question=turn.user_question,
                classification=classification,
                conversation_history=turn.conversation_history,
                client_context=_enrich_client_context_with_first_name(
                    turn.client_context, turn.user_ctx,
                ),
            )
            trace_response_preview("general_chat_service response (fallback)", reply)
            return reply

