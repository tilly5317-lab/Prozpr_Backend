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
import dataclasses
import importlib
import logging
import time
import uuid

import httpx

from app.core.observability import capture_flow_completed
from app.domains.ai_engine.common import trace_line, trace_response_preview
from app.domains.ai_engine.services.flow import (
    FLOWS,
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
from app.domains.ai_engine.portfolio_gate import (
    missing_portfolio_reply,
    portfolio_data_missing as is_portfolio_data_missing,
)
from app.domains.ai_engine.planning_gate import evaluate as evaluate_planning_gate
from app.domains.ai_engine.posthog_tracing import (
    set_turn_trace_name,
    track_turn_posthog,
)
from app.domains.ai_engine.thinking import publish_turn_thinking
from app.domains.ai_engine.types import IntentDecision, ModuleOutput
from app.domains.ai_engine.usage_tracking import (
    jsonable_llm_usage,
    track_turn_llm_usage,
)
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
# uses it to strip pre-cutover refusals out of historical chat — those rows are
# tagged with the retired ``goal_planning`` intent and must not anchor the
# model. Kept here so the name stays importable from ``app.domains.ai_engine``.
_GOAL_PLANNING_SENTINEL = "isn't built into the chat yet"


# Intents whose classifier output is itself the reply (canned out-of-scope /
# stock-advice redirects). No flow runs.
_CLASSIFIER_ONLY_INTENTS: frozenset[str] = frozenset(
    {
        "out_of_scope",
        "stock_advice",
    }
)

# Intents whose chat modules register a speculative follow-up action detector
# (audit F4). Values are the module paths whose import triggers the
# @register_speculative_detector side effect — lazy, mirroring flow.py's
# convention, so the brain stays free of module-level domain deps.
_SPECULATIVE_DETECT_MODULES: dict[str, str] = {
    "asset_allocation": "app.domains.asset_allocation.services.aa_engine.chat",
    "rebalancing": "app.domains.rebalancing.services.rebal_engine.chat",
}


def _retrieve_task_result(task: asyncio.Task) -> None:
    """Done-callback: retrieve a discarded task's exception so asyncio never
    logs 'Task exception was never retrieved' for speculation we abandoned."""
    if not task.cancelled():
        task.exception()


def _start_speculative_detect(ctx: TurnContext) -> asyncio.Task | None:
    """Kick off the active intent's action detector concurrently with the
    classifier. Returns None whenever speculation doesn't apply (no active
    intent, no registered detector, no prior module run to follow up on)."""
    intent = ctx.active_intent
    module_path = _SPECULATIVE_DETECT_MODULES.get(intent or "")
    if module_path is None:
        return None
    # A detector only helps on FOLLOW-UP turns; first turns run the engine.
    if intent not in ctx.last_agent_runs:
        return None
    try:
        importlib.import_module(module_path)  # @register side effect
        from app.domains.ai_engine.chat_dispatcher import speculative_detector_for

        detector = speculative_detector_for(intent)
        if detector is None:
            return None
        task = asyncio.create_task(detector(ctx))
        task.add_done_callback(_retrieve_task_result)
        return task
    except Exception:
        # Speculation is an optimization — never let it break the turn.
        logger.exception("failed to start speculative detect for %r", intent)
        return None


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
        # The usage tracker wraps the WHOLE turn: every LangChain LLM call in
        # any module (classifier, action detectors, engines, formatter) reports
        # into one per-turn aggregate, persisted on the chat_flow telemetry row
        # by _finalize. Works through asyncio.to_thread and asyncio.wait_for
        # (contextvars propagate into threads and child tasks).
        # PostHog nests inside on the same contextvar mechanism: one $ai_trace per
        # turn, spans per LangGraph node, a generation per LLM call. distinct_id is
        # the effective user (family-member override included), so traces line up
        # with the frontend's posthog-js identify(). No-op unless POSTHOG_API_KEY
        # is set; prompts/state are redacted unless POSTHOG_LLM_CAPTURE_CONTENT.
        with track_turn_llm_usage() as usage_cb:
            with track_turn_posthog(
                distinct_id=str(turn.effective_user_id) if turn.effective_user_id else None,
                trace_id=str(uuid.uuid4()),
                # $ai_session_id is PostHog's NATIVE conversation grouping — it makes
                # every turn of a chat session navigable as one thread in the UI.
                # Safe to set here (the handler never sets it itself); contrast with
                # $ai_span_name, which the handler DOES set per event and which a key
                # here would clobber, since it does event_properties.update(properties)
                # after its own $ai_* fields. Only add $ai_* keys the handler ignores.
                properties={
                    "$ai_session_id": str(turn.session_id) if turn.session_id else None,
                },
            ):
                return await self._run_turn(turn, usage_cb)

    async def _run_turn(self, turn: ChatTurnInput, usage_cb) -> ChatBrainResult:
        sid = turn.session_id
        uid = turn.effective_user_id
        db = turn.db
        flow: list[str] = []
        t_all = time.perf_counter()

        intent: IntentDecision | None = None
        spec_task: asyncio.Task | None = None

        try:
            trace_line("--- ChatBrain.run_turn ---")
            trace_line(f"user message: {turn.user_question}")

            # Live thinking feed: each real step below publishes a line the
            # chat UI polls and shows while this POST is in flight.
            publish_turn_thinking(
                turn, 4, "Reading your conversation and financial profile…"
            )

            # ---- 1. Per-turn context ----------------------------------------
            ctx: TurnContext = await build_turn_context(turn)
            trace_line(
                f"turn_context: last_runs={list(ctx.last_agent_runs.keys())} "
                f"active_intent={ctx.active_intent}"
            )

            # ---- 1b. Speculative follow-up action-detect (audit F4) ---------
            # Runs concurrently with the classifier; attached below only if the
            # classifier confirms the active intent, else discarded in finally.
            spec_task = _start_speculative_detect(ctx)
            if spec_task is not None:
                trace_line(
                    f"speculative detect started for active_intent={ctx.active_intent}"
                )

            # ---- 2. Intent classification (always first) --------------------
            publish_turn_thinking(
                turn, 12, "Finding the intent behind your question…"
            )
            ic_out = await intent_classifier_service.run(turn, ctx, {})
            intent = ic_out.payload
            # Surface the classifier's REAL reasoning — this is the model
            # genuinely thinking aloud, not a canned line.
            publish_turn_thinking(
                turn,
                26,
                (intent.reasoning or "").strip()
                or f"Understood — treating this as a {intent.name.replace('_', ' ')} question.",
            )
            flow.append(f"identified intent: {intent.name}")
            tools_needed = tuple(
                t.value for t in getattr(intent.raw, "tools_needed", ()) or ()
            )
            if tools_needed:
                ctx = dataclasses.replace(ctx, tools_needed=tools_needed)
                flow.append(f"tools needed: {','.join(tools_needed)}")
            # Name the PostHog trace now that we know the intent — otherwise it is
            # labelled "RunnableSequence" and the trace list is unreadable.
            set_turn_trace_name(intent.name)
            trace_line(
                f"intent classifier: {intent.name} "
                f"(confidence={intent.confidence:.2f}, reasoning={intent.reasoning!r})"
            )

            # ---- 2b. Attach or discard the speculative detect ---------------
            if spec_task is not None:
                if intent.name == ctx.active_intent:
                    ctx = dataclasses.replace(ctx, speculative_detect=spec_task)
                    flow.append("speculative detect: attached")
                    trace_line("speculative detect: attached (intent unchanged)")
                else:
                    flow.append(
                        f"speculative detect: discarded "
                        f"({ctx.active_intent} → {intent.name})"
                    )
                    trace_line("speculative detect: discarded (intent changed)")

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
                        usage_cb=usage_cb,
                    )

            # ---- 3b. No portfolio yet? Ask for the statement instead --------
            # CAMS is skippable at onboarding, so a customer can reach chat with
            # nothing imported. Running a holdings-driven engine over an empty
            # portfolio yields either a technical blocking message or example
            # numbers the customer reads as their own — so answer honestly and
            # flag the turn for the add-CAMS CTA. Fails open (see the gate).
            if await is_portfolio_data_missing(db, uid, intent.name):
                flow.append("portfolio data missing — asked for a CAMS statement")
                trace_line(f"portfolio gate: no holdings for intent={intent.name}")
                return await self._finalize(
                    text=missing_portfolio_reply(intent.name),
                    intent=intent,
                    flow=flow,
                    t0=t_all,
                    db=db,
                    uid=uid,
                    sid=sid,
                    usage_cb=usage_cb,
                    portfolio_data_missing=True,
                )

            # ---- 3c. Does this turn belong to the customer's plan? ---------
            # Same position and same contract as the portfolio gate above, and
            # the same fail-open instinct. Three things route here: an open
            # question, a goal half-built, and an engine whose inputs we do not
            # have. Mid-thread fragments ("50 lakhs down", "yes add it") do not
            # classify as planning on their own, so the OPEN THREAD — not the
            # classifier — decides where they go. The recorded intent is left
            # untouched; only the route changes.
            route_intent = intent.name
            directive = await evaluate_planning_gate(db, uid, sid, intent.name)
            if directive is not None and directive.routes_to_planning:
                ctx = dataclasses.replace(ctx, planning_directive=directive)
                route_intent = "financial_planning"
                flow.append(f"financial planning — {directive.reason}")
                trace_line(
                    f"planning gate: routing to financial_planning "
                    f"(intent={intent.name}, {directive.reason})"
                )

            # ---- 4. Pick the flow -------------------------------------------
            selected = self._flow_for(intent, ctx, route_intent)
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
                    usage_cb=usage_cb,
                    outcome="failed",
                    failure_reason="timeout",
                )

            # ---- 6. The flow's result owns the reply ------------------------
            publish_turn_thinking(
                turn, 96, "Putting the finishing touches on your answer…"
            )
            return await self._finalize(
                text=final.text or "",
                intent=intent,
                flow=flow,
                t0=t_all,
                db=db,
                uid=uid,
                sid=sid,
                final=final,
                usage_cb=usage_cb,
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
                usage_cb=usage_cb,
                outcome="failed",
                failure_reason=(
                    "llm_auth_failure"
                    if _is_llm_auth_failure(exc)
                    else type(exc).__name__
                ),
            )
        finally:
            # Any speculation that was never consumed (intent changed, canned
            # short-circuit, error path) is cancelled so it can't linger.
            if spec_task is not None and not spec_task.done():
                spec_task.cancel()

    # ---------------------------------------------------------------------
    # internals
    # ---------------------------------------------------------------------

    def _flow_for(
        self,
        intent: IntentDecision,
        ctx: TurnContext,
        route_intent: str | None = None,
    ):
        """Pick the flow for this turn: the route key looked up in ``FLOWS``;
        unknown intents fall through to ``flow_general_chat``.

        ``route_intent`` is normally just ``intent.name``. The planning gate is
        the one thing that overrides it (to ``"financial_planning"``), and it
        does so by passing a different key rather than by mutating the
        classifier's verdict — the telemetry row must still record what the
        customer actually asked. (The old ``awaiting_save`` override was removed
        in the 2026-07 audit — nothing set that gate since the save-flow
        removal.)
        """
        return FLOWS.get(route_intent or intent.name, flow_general_chat)

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
        usage_cb=None,
        outcome: str = "ok",
        failure_reason: str | None = None,
        portfolio_data_missing: bool = False,
    ) -> ChatBrainResult:
        """Shape the assistant reply + write end-of-turn telemetry."""
        ms = int((time.perf_counter() - t0) * 1000)
        trace_response_preview(
            "final assistant message sent to client",
            text,
            max_chars=1200,
        )
        # All LLM calls for this turn have completed by now (success, timeout,
        # or error path), so the tracker holds the turn's full aggregate.
        llm_usage = jsonable_llm_usage(usage_cb.usage_metadata) if usage_cb else None
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
                    llm_usage=llm_usage,
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
        # The turn's durable outcome. Here rather than at each exit because all
        # four of them already funnel through _finalize.
        capture_flow_completed(
            intent=intent.name if intent else None,
            outcome=outcome,
            failure_reason=failure_reason,
            duration_ms=ms,
            distinct_id=uid,
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
            portfolio_data_missing=portfolio_data_missing,
            planning_saved=(final.side_effects or {}).get("planning_saved")
            if final
            else None,
            goal_saved=(final.side_effects or {}).get("goal_saved") if final else None,
            goal_removed=(final.side_effects or {}).get("goal_removed")
            if final
            else None,
        )
