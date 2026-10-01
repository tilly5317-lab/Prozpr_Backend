"""Minimal TurnContext factory for detector/override tests.

A plain module (NOT conftest — conftests are not importable) so both the
rebal_engine tests and the AI_Agents detector eval can build the same context.
No DB, no user graph: fields irrelevant to detection are stubbed.
"""

from __future__ import annotations

import uuid
from typing import Any

from app.domains.ai_engine.turn_context import TurnContext


def make_detector_ctx(question: str, *, user_ctx: Any = None) -> TurnContext:
    return TurnContext(
        user_ctx=user_ctx,
        user_question=question,
        conversation_history=[],
        client_context=None,
        session_id=uuid.uuid4(),
        db=None,
        effective_user_id=uuid.uuid4(),
        last_agent_runs={},
        active_intent=None,
    )


def make_last_run() -> Any:
    """Minimal AgentRunRecord stub for `_detect_rebal_action(last_run, ctx)`.

    The detector slims/digests ``output_payload``; an empty-but-shaped payload
    exercises the pure-question path without a real prior run.
    """
    from datetime import datetime, timezone

    from app.domains.ai_engine.turn_context import AgentRunRecord

    return AgentRunRecord(
        id=uuid.uuid4(),
        module="rebalancing",
        intent_detected="rebalancing",
        input_payload=None,
        output_payload={
            "rebalancing_response": {"totals": {}, "subgroups": [], "rows": []},
            "goal_buckets": None,
        },
        created_at=datetime.now(timezone.utc),
    )
