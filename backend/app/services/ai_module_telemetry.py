"""Application service — `ai_module_telemetry.py`.

Encapsulates business logic consumed by FastAPI routers. Uses database sessions, optional external APIs, and other services; should remain free of route-specific HTTP details (status codes live in routers).
"""


from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

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
    spine_mode: str | None = None,
    duration_ms: int | None = None,
    extra: dict[str, Any] | None = None,
    emit_standard_log: bool = True,
) -> None:
    """
    Optionally emit AILAX_AI_MODULE_RUN; always persist one row when db is set.
    Use emit_standard_log=False when a higher-level AILAX_CHAT_FLOW line is logged instead.
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
        return
    row = ChatAiModuleRun(
        user_id=user_id,
        session_id=session_id,
        module=module,
        reason=reason,
        intent_detected=intent_detected,
        spine_mode=spine_mode,
        duration_ms=duration_ms,
        extra=extra,
    )
    db.add(row)
    await db.flush()


async def log_chat_turn_flow_summary(
    db: AsyncSession | None,
    *,
    user_id: uuid.UUID,
    session_id: uuid.UUID,
    intent: str | None,
    steps: list[str],
    duration_ms: int | None = None,
) -> None:
    """One readable line per chat turn (grep: AILAX_CHAT_FLOW). Also stored as module=chat_flow."""
    text = " → ".join(steps)
    logger.info(
        "AILAX_CHAT_FLOW user_id=%s session_id=%s intent=%s | %s | duration_ms=%s",
        user_id,
        session_id,
        intent,
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
        duration_ms=duration_ms,
        emit_standard_log=False,
    )
