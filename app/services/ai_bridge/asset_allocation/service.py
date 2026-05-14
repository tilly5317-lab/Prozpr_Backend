"""Asset-allocation bridge — runs the engine and persists results.

When ``AI_Agents/src/asset_allocation_pydantic`` is installed the engine runs
in a worker thread; otherwise the bridge returns a user-friendly offline message.
Public API is stable for imports from ``ailax_flow``, ``rebalancing/service``,
and ``chat.py``.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.ai_bridge.asset_allocation.input_builder import (
    build_asset_allocation_input_for_user,
)
from app.services.ai_bridge.asset_allocation.persistence import (
    save_asset_allocation_from_engine_output,
)
from app.services.ai_bridge.common import ensure_ai_agents_path, trace_line

logger = logging.getLogger(__name__)

# ── Try to import the engine (safe no-op when package is missing) ───────

ensure_ai_agents_path()

_engine_available = False
try:
    from asset_allocation_pydantic.pipeline import run_allocation_with_state  # type: ignore[import-not-found]
    _engine_available = True
except ImportError:
    logger.info("asset_allocation_pydantic not found — allocation bridge runs in stub mode")

# ── User-facing messages ────────────────────────────────────────────────

MSG_ALLOCATION_MISSING_DOB = (
    "I need your date of birth to build a personalised allocation — it anchors "
    "your risk profile and time horizon. Head to your profile, add it, then "
    "ask me again."
)

MSG_ALLOCATION_UPGRADING = (
    "The goal-based allocation engine is being upgraded. "
    "Please check back soon — you can still ask about your portfolio "
    "or general investing topics."
)

# ── Result dataclass ────────────────────────────────────────────────────


@dataclass(frozen=True)
class AllocationRunOutcome:
    """Immutable outcome of a single allocation attempt."""

    result: Any | None
    blocking_message: str | None = None
    asset_allocation_run_id: uuid.UUID | None = None


# ── Helpers (used by ailax_flow) ────────────────────────────────────────


def build_fallback_brief(output: Any, spine_mode: str | None) -> str:
    """Produce a chat-ready brief from engine output. Stub — always upgrading."""
    del output, spine_mode
    return MSG_ALLOCATION_UPGRADING


def build_aa_facts_pack(output: Any) -> dict[str, Any]:
    """Build a facts dict for the answer formatter. Stub — empty facts."""
    del output
    return {"engine_status": "offline", "message": MSG_ALLOCATION_UPGRADING}


async def compose_allocation_chat_reply(
    user_question: str,
    deterministic_brief: str,
    mode: str,
) -> str | None:
    """Tailored chat reply on top of a deterministic brief. Stub — returns None."""
    del user_question, deterministic_brief, mode
    return None


# ── Engine runner ───────────────────────────────────────────────────────


def _run_engine(engine_input: Any) -> Any:
    """Synchronous engine call — intended to be run in a worker thread.

    ``run_allocation_with_state`` returns ``(state_dict, GoalAllocationOutput)``;
    we only need the output for persistence and downstream use.
    """
    _state, output = run_allocation_with_state(engine_input)
    return output


async def _call_engine(user: Any) -> tuple[Any | None, dict[str, Any]]:
    """Build input from the User ORM graph and run the engine.

    Returns ``(engine_result, input_payload)``.
    ``engine_result`` is None when the engine isn't available or fails.
    """
    if not _engine_available:
        return None, {}, {}

    try:
        engine_input, debug_dict, allocation_snapshot = build_asset_allocation_input_for_user(user)
    except Exception:
        logger.exception("failed to build allocation input from user")
        return None, {}, {}

    trace_line(f"asset_allocation engine input built — goals={debug_dict.get('goal_count')}")

    try:
        result = await asyncio.to_thread(_run_engine, engine_input)
    except Exception:
        logger.exception("asset_allocation engine raised")
        return None, debug_dict, allocation_snapshot

    trace_line("asset_allocation engine finished")
    return result, debug_dict, allocation_snapshot


# ── Core compute path ───────────────────────────────────────────────────


async def compute_allocation_result(
    user,
    user_question: str,
    *,
    db: AsyncSession | None = None,
    persist_recommendation: bool = False,
    acting_user_id: uuid.UUID | None = None,
    chat_session_id: uuid.UUID | None = None,
    spine_mode: str | None = None,
    input_payload: dict[str, Any] | None = None,
) -> AllocationRunOutcome:
    """Run the allocation engine and optionally persist the result.

    When ``persist_recommendation=True`` + a ``db`` session, the output is
    written to the ``asset_allocation_*`` tables.
    """
    trace_line("module: asset_allocation")

    if getattr(user, "date_of_birth", None) is None:
        return AllocationRunOutcome(result=None, blocking_message=MSG_ALLOCATION_MISSING_DOB)

    # ── run engine ──
    result, engine_debug, allocation_snapshot = await _call_engine(user)
    merged_payload = {
        **allocation_snapshot,
        **(input_payload or {}),
        "allocation_engine_debug": engine_debug,
    }
    asset_allocation_run_id: uuid.UUID | None = None

    # ── persist when we have a result + caller asked for it ──
    if result is not None and persist_recommendation and db is not None:
        uid = acting_user_id or getattr(user, "id", None)
        if uid is not None:
            goal_id_map = _goal_ids_by_name(user)
            try:
                asset_allocation_run_id = await save_asset_allocation_from_engine_output(
                    db,
                    user_id=uid,
                    portfolio_id=None,
                    chat_session_id=chat_session_id,
                    pipeline_source="asset_allocation_pydantic",
                    spine_mode=spine_mode,
                    user_question=user_question,
                    input_payload=merged_payload,
                    engine_result=result,
                    financial_goal_ids_by_name=goal_id_map,
                )
            except Exception:
                logger.exception("asset allocation persist failed; continuing without DB ids")

    if result is not None:
        return AllocationRunOutcome(
            result=result,
            blocking_message=None,
            asset_allocation_run_id=asset_allocation_run_id,
        )

    return AllocationRunOutcome(result=None, blocking_message=MSG_ALLOCATION_UPGRADING)


def _goal_ids_by_name(user: Any) -> dict[str, uuid.UUID] | None:
    """Map goal_name → FinancialGoal.id for linking run targets to canonical goals."""
    goals = getattr(user, "financial_goals", None) or []
    if not goals:
        return None
    return {
        getattr(g, "goal_name", ""): getattr(g, "id", None)
        for g in goals
        if getattr(g, "goal_name", None) and getattr(g, "id", None)
    }


# ── Standalone HTTP helper ──────────────────────────────────────────────


async def generate_asset_allocation_response(
    user,
    user_question: str,
    *,
    db: AsyncSession | None = None,
    persist_recommendation: bool = False,
    acting_user_id: uuid.UUID | None = None,
) -> str:
    """HTTP-facing wrapper — returns a plain string for the router."""
    outcome = await compute_allocation_result(
        user,
        user_question,
        db=db,
        persist_recommendation=persist_recommendation,
        acting_user_id=acting_user_id,
        chat_session_id=None,
        spine_mode="api_asset_allocation",
    )
    if outcome.blocking_message:
        return outcome.blocking_message
    return MSG_ALLOCATION_UPGRADING
