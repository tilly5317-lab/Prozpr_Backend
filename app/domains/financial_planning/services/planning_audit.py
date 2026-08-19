"""The decision trail for one planning turn.

Every other AI module records what it did through
``chat.services.ai_module_telemetry.record_ai_module_run`` — one
``chat_ai_module_runs`` row plus a greppable log line — and this domain reports
through exactly the same door, under ``module="financial_planning"``. What is
domain-specific is WHAT gets recorded, and that is driven by the three questions
someone actually asks six weeks later, looking at a figure that seems wrong:

  1. **What did we understand?** The operations the extractor produced, with the
     customer's own words beside each one. (`reason="read"`)
  2. **Why is THAT table being written?** Nobody chooses a table; a field key
     chooses it, via the registry. So every staged and written value carries
     ``field_key -> table.column``, which is the whole answer.
     (`reason="staged"`, `reason="write"`)
  3. **What actually changed, and what did that set off?** Previous and new
     value per column, the basis when it was derived from a figure we already
     held, and every downstream effect considered — including the ones that did
     NOT run and why not. (`reason="write"`)

Three rows per turn at most, and a turn that changes nothing writes one. Values
are recorded because this is the audit trail — but the utterance is redacted
before it is stored, so the trail never becomes a second copy of the
identifiers ``privacy`` exists to strip.

Best-effort throughout: telemetry must never cost a turn. Every entry point
swallows its own exceptions, and the writes ride the caller's transaction the
same way the rest of the domain does.
"""

from __future__ import annotations

import logging
from typing import Any

from app.domains.financial_planning.services import privacy
from app.domains.financial_planning.services.operations import Operation
from app.domains.profile.services.profile_field_registry import spec

logger = logging.getLogger("ailax.ai_bridge")

MODULE = "financial_planning"


# ---------------------------------------------------------------------------
# Describing one operation
# ---------------------------------------------------------------------------


def describe(op: Operation) -> dict[str, Any]:
    """One operation, in the shape someone debugging it needs.

    ``target_table`` / ``target_column`` are resolved from the registry rather
    than restated, because that resolution IS the answer to "why is this table
    being written" — the extractor never names a table, it names a field, and
    the registry owns where that field lives.
    """
    out: dict[str, Any] = {
        "target": op.target,
        "verb": op.verb,
        "confidence": round(float(op.confidence), 3),
    }
    if op.field_key:
        out["field_key"] = op.field_key
        fs = spec(op.field_key)
        if fs is not None:
            out["target_table"] = fs.table
            out["target_column"] = fs.column
            out["stored_as"] = fs.unit
    if op.goal_ref:
        out["goal_ref"] = op.goal_ref
    if op.slots:
        out["goal_slots"] = {k: v for k, v in op.slots.items() if v is not None}
    if op.value is not None:
        out["value"] = op.value
    if op.basis:
        # Only ever set for a relative change, and it is the first thing to look
        # at when a derived figure looks wrong.
        out["derived_from"] = op.basis
    if op.verbatim:
        out["heard"] = op.verbatim
    return out


def _change_line(row: dict[str, Any]) -> str:
    """One change, short enough for a log line.

    A goal row carries all nineteen of its columns in ``previous_value``, which
    is exactly what makes a delete undoable and exactly what makes it unreadable
    in a log. So the line says what happened to it and the persisted payload
    keeps the object.
    """
    if row["table"] == "goals":
        name = _goal_name(row) or row["field_key"]
        if row["previous"] is None:
            what = "added"
        elif row["new"] is None:
            what = "removed"
        else:
            what = "updated"
        return f"goals[{name}]: {what}"
    return f"{row['table']}.{row['column']}: {row['previous']!r}->{row['new']!r}"


def _goal_name(row: dict[str, Any]) -> str | None:
    for side in (row.get("new"), row.get("previous")):
        if isinstance(side, dict):
            name = side.get("name") or side.get("goal_name")
            if name:
                return str(name)
    return None


def _short(op: Operation) -> str:
    """``profile.set annual_income->personal_finance_profiles.annual_income``"""
    fs = spec(op.field_key) if op.field_key else None
    where = f"->{fs.table}.{fs.column}" if fs is not None else ""
    subject = op.field_key or op.goal_ref or op.target
    return f"{op.target}.{op.verb} {subject}{where}"


# ---------------------------------------------------------------------------
# 1. What we understood
# ---------------------------------------------------------------------------


async def log_read(ctx, read, *, ask, draft) -> None:
    """What the extractor made of the message, before anything is held."""
    try:
        ops = [describe(o) for o in read.operations]
        unread = [
            {"field_key": u.field_key, "reason": u.reason, "heard": u.verbatim}
            for u in read.unread
        ]
        logger.info(
            "AILAX_FP_READ user_id=%s session_id=%s kind=%s ops=[%s] unread=%s "
            "open_ask=%s draft=%s clarify=%s",
            ctx.effective_user_id,
            ctx.session_id,
            read.kind,
            " ; ".join(_short(o) for o in read.operations) or "-",
            ",".join(f"{u['field_key']}:{u['reason']}" for u in unread) or "-",
            getattr(ask, "field_key", None),
            getattr(draft, "stage", None),
            bool(read.clarification),
        )
        await _record(
            ctx.db,
            ctx.effective_user_id,
            ctx.session_id,
            reason="read",
            input_payload={
                # Redacted: the trail must not become a second copy of the
                # identifiers privacy exists to strip out of the prompt.
                "utterance": privacy.redact(ctx.user_question)[:1000],
                "open_ask_field": getattr(ask, "field_key", None),
                "open_ask_status": getattr(ask, "status", None),
                "goal_draft_stage": getattr(draft, "stage", None),
            },
            output_payload={
                "kind": read.kind,
                "operations": ops,
                "unread": unread,
                "unchanged_fields": list(read.unchanged),
                "clarification": read.clarification,
                "wants_projection": read.wants_projection,
            },
        )
    except Exception:
        logger.exception("financial_planning: failed to record the read")


# ---------------------------------------------------------------------------
# 2. What is being held, and where it would go
# ---------------------------------------------------------------------------


async def log_staged(ctx, *, fields: dict[str, Any], deletes: list[dict[str, str]]) -> None:
    """Values understood and held, with the table each one is destined for.

    Recorded separately from the write because the gap between them is where
    the interesting failures live: a value staged and never confirmed should
    leave a ``staged`` row and NO ``write`` row, and that pair is how you tell
    "the customer declined" from "we dropped it".
    """
    if not fields and not deletes:
        return
    try:
        held = []
        for key, entry in fields.items():
            fs = spec(key)
            held.append(
                {
                    "field_key": key,
                    "verb": entry.get("verb"),
                    "value": entry.get("value"),
                    "would_write": f"{fs.table}.{fs.column}" if fs else None,
                    "derived_from": entry.get("basis"),
                    "confidence": entry.get("confidence"),
                    "source": entry.get("source"),
                }
            )
        logger.info(
            "AILAX_FP_STAGED user_id=%s session_id=%s held=[%s] pending_deletes=[%s] "
            "-- nothing written yet",
            ctx.effective_user_id,
            ctx.session_id,
            " ; ".join(
                f"{h['field_key']}={h['value']}->{h['would_write']}" for h in held
            )
            or "-",
            ",".join(d.get("name", "?") for d in deletes) or "-",
        )
        await _record(
            ctx.db,
            ctx.effective_user_id,
            ctx.session_id,
            reason="staged",
            output_payload={
                "held_fields": held,
                "pending_goal_deletes": [
                    {"goal_id": d.get("goal_id"), "name": d.get("name")}
                    for d in deletes
                ],
                "written": False,
            },
        )
    except Exception:
        logger.exception("financial_planning: failed to record the staged values")


# ---------------------------------------------------------------------------
# 3. What actually changed, and what it set off
# ---------------------------------------------------------------------------


async def log_write(ctx, *, writes: list[Any], report, projection_queued: bool) -> None:
    """The committed change set, column by column, plus every effect considered.

    ``writes`` are the audit rows themselves, so this cannot drift from what was
    persisted — and it is the same list ``downstream`` decided from, which is
    what makes "why did X re-run?" answerable from one row.
    """
    try:
        changed = [
            {
                "table": w.table_name,
                "column": w.column_name,
                "field_key": w.field_key,
                "previous": w.previous_value,
                "new": w.new_value,
                "source": w.source,
                "confidence": float(w.confidence) if w.confidence is not None else None,
                "heard": w.verbatim,
                "undo_write_id": str(w.id),
            }
            for w in writes
        ]
        logger.info(
            "AILAX_FP_WRITE user_id=%s session_id=%s changed=[%s] | effects: %s | "
            "projection_queued=%s",
            ctx.effective_user_id,
            ctx.session_id,
            " ; ".join(_change_line(c) for c in changed) or "none",
            report.as_line() if report is not None else "none",
            projection_queued,
        )
        await _record(
            ctx.db,
            ctx.effective_user_id,
            ctx.session_id,
            reason="write",
            output_payload={
                "changed": changed,
                "effects": report.as_payload() if report is not None else [],
                "projection_queued": projection_queued,
            },
            extra={
                "tables_touched": sorted({c["table"] for c in changed}),
                "effects_fired": report.fired if report is not None else [],
                "effects_skipped": report.skipped if report is not None else [],
            },
        )
    except Exception:
        logger.exception("financial_planning: failed to record the write")


# ---------------------------------------------------------------------------
# 4. Taking it back
# ---------------------------------------------------------------------------


async def log_undo(db, user_id, session_id, *, write, report) -> None:
    """A reversal, recorded as its own change set.

    Undo is a write, so it gets the same treatment: what went back to what, and
    which effects re-ran because of it. Without this row the trail shows a value
    being set and then, apparently, changing on its own.
    """
    try:
        logger.info(
            "AILAX_FP_UNDO user_id=%s session_id=%s restored=%s.%s %r->%r | effects: %s",
            user_id,
            session_id,
            write.table_name,
            write.column_name,
            write.new_value,
            write.previous_value,
            report.as_line() if report is not None else "none",
        )
        await _record(
            db,
            user_id,
            session_id,
            reason="undo",
            output_payload={
                "reversed_write_id": str(write.id),
                "table": write.table_name,
                "column": write.column_name,
                "field_key": write.field_key,
                # Read in the direction the undo moved it.
                "from": write.new_value,
                "to": write.previous_value,
                "effects": report.as_payload() if report is not None else [],
            },
            extra={
                "tables_touched": [write.table_name],
                "effects_fired": report.fired if report is not None else [],
            },
        )
    except Exception:
        logger.exception("financial_planning: failed to record the undo")


# ---------------------------------------------------------------------------
# The one door out
# ---------------------------------------------------------------------------


async def _record(
    db,
    user_id,
    session_id,
    *,
    reason: str,
    input_payload: dict[str, Any] | None = None,
    output_payload: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    """Persist one ``chat_ai_module_runs`` row, exactly as every other module does.

    ``emit_standard_log=False`` because this module writes its own, richer line
    above — the generic AILAX_AI_MODULE_RUN line carries no column names, and
    column names are the entire point here.
    """
    from app.domains.chat.services.ai_module_telemetry import record_ai_module_run

    await record_ai_module_run(
        db,
        user_id=user_id,
        session_id=session_id,
        module=MODULE,
        reason=reason,
        intent_detected=MODULE,
        input_payload=_jsonable(input_payload),
        output_payload=_jsonable(output_payload),
        extra=_jsonable(extra),
        emit_standard_log=False,
    )


def _jsonable(value: Any) -> Any:
    """JSONB-safe. Dates and Decimals reach here from the profile columns."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


__all__ = ["MODULE", "describe", "log_read", "log_staged", "log_undo", "log_write"]
