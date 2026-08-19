"""CRUD on the customer's plan inputs — the profile half of the domain.

Every write goes through ``profile.services.profile_write_router``, which owns
validation and the table dispatch, so chat and ``/profile/complete`` cannot
produce two vocabularies for one column. What this module adds on top is the
part that only chat needs:

  * **staging.** A sentence in a conversation is a proposal, not a form
    submission. Values are held on the open ask and written only once the
    customer agrees in words — so nothing derived from prose reaches a profile
    table without a yes.
  * **an audit row per write**, carrying the previous value. That is what makes
    undo possible, and undo is what makes writing to a financial profile from a
    chat message safe. It is also the input to ``downstream`` — the re-score and
    the plan-cache invalidation fire off what was ACTUALLY written, never off
    what was merely discussed.
  * **read and clear**, which the form has no equivalent of: the customer can
    ask what we hold and tell us to drop it, in the same surface they set it.

Commit-free: the chat router owns the transaction.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.ai_engine.common import format_inr_indian
from app.domains.financial_planning.models import (
    SOURCE_CHAT_ANSWER,
    SOURCE_CHAT_VOLUNTEERED,
)
from app.domains.financial_planning.services import planning_state as state
from app.domains.financial_planning.services.operations import Operation
from app.domains.profile.services.profile_completeness_service import (
    ProfileSnapshot,
    load_snapshot,
)
from app.domains.profile.services.profile_field_registry import FieldSpec, spec
from app.domains.profile.services.profile_write_router import (
    FieldValidationError,
    apply_field,
    restore_field,
    validate_value,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------


def display_value(fs: FieldSpec, value: Any) -> str:
    """How a value is shown back to the customer."""
    if value is None:
        return "not set"
    if fs.input_kind == "money":
        formatted = format_inr_indian(value) or str(value)
        if fs.unit == "inr_per_month":
            return f"{formatted} a month"
        if fs.unit == "inr_per_year":
            return f"{formatted} a year"
        return formatted
    if fs.input_kind == "percent":
        return f"{float(value):g}%"
    if fs.input_kind == "integer":
        return f"{int(value)} years" if fs.unit == "years" else str(int(value))
    if fs.input_kind == "date":
        return value.isoformat() if hasattr(value, "isoformat") else str(value)
    return str(value)


def short_label(fs: FieldSpec) -> str:
    """A chip-sized name for the field — the question is far too long for one."""
    return fs.key.replace("_", " ").capitalize()


def chip(fs: FieldSpec, value: Any, *, basis: str | None = None) -> dict[str, str]:
    """One line of "here is what I have", for the reply and for the UI chip."""
    out = {
        "field_key": fs.key,
        "label": short_label(fs),
        "display_value": display_value(fs, value),
    }
    if basis:
        out["basis"] = basis
    return out


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


async def snapshot(db: AsyncSession, user_id: uuid.UUID) -> ProfileSnapshot:
    return await load_snapshot(db, user_id)


def read_fields(snap: ProfileSnapshot, field_keys: list[str]) -> list[dict[str, str]]:
    """What we hold for these fields, ready to read back.

    A field with nothing stored is returned as "not set" rather than dropped —
    "I don't have that on file" is the answer to the question they asked.
    """
    out: list[dict[str, str]] = []
    for key in field_keys:
        fs = spec(key)
        if fs is None:
            continue
        out.append(chip(fs, snap.values.get(key)))
    return out


# ---------------------------------------------------------------------------
# Staging — held, not written
# ---------------------------------------------------------------------------


def stage(
    ops: list[Operation],
    *,
    asked_key: str | None,
    already_staged: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[dict[str, str]], list[str]]:
    """Validate profile operations and HOLD them.

    Returns ``(staged, chips, rejected_keys)``. Staged values belong to the
    CONVERSATION rather than to the question that happened to surface them, so
    they carry forward across asks until the customer confirms or backs out.
    """
    staged: dict[str, Any] = dict(already_staged or {})
    chips: list[dict[str, str]] = []
    rejected: list[str] = []

    for op in ops:
        fs = spec(op.field_key or "")
        if fs is None:
            continue
        if op.verb == "clear":
            staged[fs.key] = {
                "value": None,
                "verb": "clear",
                "confidence": op.confidence,
                "verbatim": op.verbatim,
                "source": op.source,
            }
            chips.append(chip(fs, None, basis="clearing what we had"))
            continue
        try:
            value = validate_value(fs.key, op.value)
        except FieldValidationError as exc:
            logger.info("planning op rejected: %s", exc)
            rejected.append(fs.key)
            continue
        staged[fs.key] = {
            "value": state.jsonable(value),
            "verb": op.verb,
            "basis": op.basis,
            "confidence": op.confidence,
            "verbatim": op.verbatim,
            "source": (
                op.source
                if op.verb == "adjust"
                else (SOURCE_CHAT_ANSWER if fs.key == asked_key else SOURCE_CHAT_VOLUNTEERED)
            ),
        }
        chips.append(chip(fs, value, basis=op.basis))

    return staged, chips, rejected


def staged_chips(staged: dict[str, Any] | None) -> list[dict[str, str]]:
    """Everything held so far, ready to read back."""
    out: list[dict[str, str]] = []
    for key, held in (staged or {}).items():
        fs = spec(key)
        if fs is None:
            continue
        out.append(chip(fs, held.get("value"), basis=held.get("basis")))
    return out


# ---------------------------------------------------------------------------
# Commit
# ---------------------------------------------------------------------------


async def commit(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    session_id: uuid.UUID | None,
    ask_id: uuid.UUID | None,
    staged: dict[str, Any] | None,
) -> tuple[list[dict[str, str]], list[Any]]:
    """The customer agreed — now, and only now, write to the profile tables.

    Returns the chips to show them and the audit rows just written. The caller
    hands those rows to ``downstream.fire``; nothing here decides what to
    re-run, so a write and its consequences cannot get out of step.
    """
    saved: list[dict[str, str]] = []
    writes: list[Any] = []
    for key, held in (staged or {}).items():
        fs = spec(key)
        if fs is None:
            continue
        value = held.get("value")
        try:
            if held.get("verb") == "clear":
                # A clear is the one write that bypasses validation, because
                # NULL is not a value any field would accept.
                previous = (await load_snapshot(db, user_id)).values.get(key)
                await restore_field(db, user_id, key, None)
                table, column, new_value = fs.table, fs.column, None
            else:
                result = await apply_field(db, user_id, key, value)
                previous, table, column, new_value = (
                    result.previous,
                    result.table,
                    result.column,
                    result.value,
                )
        except FieldValidationError as exc:
            logger.info("staged value no longer valid at commit: %s", exc)
            continue

        writes.append(
            await state.record_write(
                db,
                user_id=user_id,
                session_id=session_id,
                ask_id=ask_id,
                field_key=key,
                table_name=table,
                column_name=column,
                previous=previous,
                value=new_value,
                source=held.get("source") or SOURCE_CHAT_ANSWER,
                confidence=held.get("confidence"),
                verbatim=held.get("verbatim"),
            )
        )
        saved.append(chip(fs, new_value, basis=held.get("basis")))

    return saved, writes


__all__ = [
    "chip",
    "commit",
    "display_value",
    "read_fields",
    "short_label",
    "snapshot",
    "stage",
    "staged_chips",
]
