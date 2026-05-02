"""Application service — `ai_module_telemetry.py`.

Encapsulates business logic consumed by FastAPI routers. Uses database sessions, optional external APIs, and other services; should remain free of route-specific HTTP details (status codes live in routers).
"""


from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import SQLAlchemyError

from app.models.chat_ai_module_run import ChatAiModuleRun

logger = logging.getLogger("ailax.ai_bridge")


async def record_ai_module_run(
    db: AsyncSession | None,
    *,
    user_id: uuid.UUID,
    session_id: uuid.UUID | None,
    module: str,
    reason: str,
    intent_detected: str | None = None,
    intent_confidence: float | None = None,
    spine_mode: str | None = None,
    duration_ms: int | None = None,
    extra: dict[str, Any] | None = None,
    input_payload: dict[str, Any] | None = None,
    output_payload: dict[str, Any] | None = None,
    emit_standard_log: bool = True,
    # New formatter telemetry — all nullable.
    formatter_invoked: bool | None = None,
    formatter_succeeded: bool | None = None,
    formatter_latency_ms: int | None = None,
    formatter_error_class: str | None = None,
    action_mode: str | None = None,
) -> uuid.UUID | None:
    """
    Optionally emit AILAX_AI_MODULE_RUN; always persist one row when db is set.
    Use emit_standard_log=False when a higher-level AILAX_CHAT_FLOW line is logged instead.
    Returns the new row's id (or None when db is None).
    """
    if emit_standard_log:
        logger.info(
            "AILAX_AI_MODULE_RUN module=%s reason=%s user_id=%s session_id=%s intent=%s spine_mode=%s duration_ms=%s",
            module,
            reason.replace("\n", " ")[:500],
            user_id,
            session_id,
            intent_detected,
            spine_mode,
            duration_ms,
        )
    if db is None:
        return None
    try:
        # Keep telemetry best-effort: a failed audit insert must not break chat flow.
        async with db.begin_nested():
            row = ChatAiModuleRun(
                user_id=user_id,
                session_id=session_id,
                module=module,
                reason=reason,
                intent_detected=intent_detected,
                intent_confidence=intent_confidence,
                spine_mode=spine_mode,
                duration_ms=duration_ms,
                extra=extra,
                input_payload=input_payload,
                output_payload=output_payload,
                formatter_invoked=formatter_invoked,
                formatter_succeeded=formatter_succeeded,
                formatter_latency_ms=formatter_latency_ms,
                formatter_error_class=formatter_error_class,
                action_mode=action_mode,
            )
            db.add(row)
            await db.flush()
            return row.id
    except SQLAlchemyError as exc:
        logger.warning(
            "Skipping AI module telemetry write due to DB transaction state/session error: %s",
            exc,
        )
        return None


async def log_chat_turn_flow_summary(
    db: AsyncSession | None,
    *,
    user_id: uuid.UUID,
    session_id: uuid.UUID,
    intent: str | None,
    steps: list[str],
    duration_ms: int | None = None,
    intent_confidence: float | None = None,
) -> None:
    """One readable line per chat turn (grep: AILAX_CHAT_FLOW). Also stored as module=chat_flow."""
    text = " → ".join(steps)
    logger.info(
        "AILAX_CHAT_FLOW user_id=%s session_id=%s intent=%s confidence=%s | %s | duration_ms=%s",
        user_id,
        session_id,
        intent,
        f"{intent_confidence:.2f}" if intent_confidence is not None else None,
        text,
        duration_ms,
    )
    await record_ai_module_run(
        db,
        user_id=user_id,
        session_id=session_id,
        module="chat_flow",
        reason=text,
        intent_detected=intent,
        intent_confidence=intent_confidence,
        duration_ms=duration_ms,
        emit_standard_log=False,
    )
