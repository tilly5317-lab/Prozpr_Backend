"""The decision trail.

These pin the three things someone actually needs six weeks later, looking at a
figure that seems wrong: what we understood, why THAT table was written, and
what the write set off. Telemetry that is subtly wrong is worse than none — it
sends the reader down the wrong path with confidence.
"""

from __future__ import annotations

import json
import unittest
import uuid
from unittest.mock import AsyncMock, patch

from app.domains.financial_planning.services import planning_audit as audit
from app.domains.financial_planning.services.downstream import (
    Change,
    EffectOutcome,
    FireReport,
)
from app.domains.financial_planning.services.operations import Operation
from app.domains.financial_planning.services.planning_extractor import (
    PlanningRead,
    UnreadOperation,
)

_RECORD = "app.domains.chat.services.ai_module_telemetry.record_ai_module_run"


class _Ctx:
    """The subset of TurnContext the audit module reads."""

    db = None
    effective_user_id = uuid.UUID("11111111-1111-1111-1111-111111111111")
    session_id = uuid.UUID("22222222-2222-2222-2222-222222222222")
    user_question = "my salary went up 20%, PAN ABCDE1234F"


class _Write:
    """The subset of a PlanningWrite row the audit module reads."""

    def __init__(self, **kw):
        self.id = uuid.uuid4()
        self.table_name = kw.get("table_name", "personal_finance_profiles")
        self.column_name = kw.get("column_name", "annual_income")
        self.field_key = kw.get("field_key", "annual_income")
        self.previous_value = kw.get("previous_value", 3_000_000)
        self.new_value = kw.get("new_value", 3_600_000)
        self.source = kw.get("source", "chat_relative")
        self.confidence = kw.get("confidence", 0.95)
        self.verbatim = kw.get("verbatim", "salary went up 20%")


class TestDescribeOperation(unittest.TestCase):
    """A field key chooses the table, so the record has to show that step."""

    def test_it_resolves_the_table_the_write_will_land_in(self):
        d = audit.describe(
            Operation(
                target="profile",
                verb="set",
                field_key="annual_income",
                value=3_200_000,
                confidence=0.94,
            )
        )
        self.assertEqual(d["field_key"], "annual_income")
        self.assertEqual(d["target_table"], "personal_finance_profiles")
        self.assertEqual(d["target_column"], "annual_income")
        self.assertEqual(d["stored_as"], "inr_per_year")

    def test_a_relative_change_records_what_it_was_derived_from(self):
        d = audit.describe(
            Operation(
                target="profile",
                verb="adjust",
                field_key="annual_income",
                value=3_600_000,
                basis="20% increase on the ₹30,00,000 on file",
                confidence=0.92,
            )
        )
        # The first thing to look at when a derived figure looks wrong.
        self.assertIn("30,00,000", d["derived_from"])

    def test_a_goal_operation_names_no_table(self):
        # Goals do not go through the field registry, so there is nothing to
        # resolve — claiming a table here would be a lie.
        d = audit.describe(
            Operation(
                target="goal",
                verb="delete",
                goal_ref="Europe trip",
                confidence=0.9,
            )
        )
        self.assertEqual(d["goal_ref"], "Europe trip")
        self.assertNotIn("target_table", d)


class TestLogRead(unittest.IsolatedAsyncioTestCase):
    async def _capture(self, read):
        rec = AsyncMock()
        with patch(_RECORD, rec):
            await audit.log_read(_Ctx(), read, ask=None, draft=None)
        return rec.await_args.kwargs if rec.await_args else None

    async def test_it_records_the_operations_and_the_module(self):
        read = PlanningRead(
            kind="state",
            operations=[
                Operation(
                    target="profile",
                    verb="adjust",
                    field_key="annual_income",
                    value=3_600_000,
                    basis="20% increase on the ₹30,00,000 on file",
                    confidence=0.92,
                )
            ],
        )
        kw = await self._capture(read)
        self.assertEqual(kw["module"], "financial_planning")
        self.assertEqual(kw["reason"], "read")
        op = kw["output_payload"]["operations"][0]
        self.assertEqual(op["target_table"], "personal_finance_profiles")

    async def test_the_stored_utterance_is_redacted(self):
        # The trail must not become a second copy of the identifiers the
        # extractor boundary exists to strip.
        kw = await self._capture(PlanningRead(kind="state"))
        self.assertNotIn("ABCDE1234F", kw["input_payload"]["utterance"])
        self.assertIn("20%", kw["input_payload"]["utterance"])

    async def test_what_we_could_not_read_is_recorded_too(self):
        read = PlanningRead(
            kind="state",
            unread=[UnreadOperation("annual_income", "no_baseline", verbatim="up 20%")],
        )
        kw = await self._capture(read)
        self.assertEqual(kw["output_payload"]["unread"][0]["reason"], "no_baseline")

    async def test_a_telemetry_failure_never_costs_the_turn(self):
        with patch(_RECORD, AsyncMock(side_effect=RuntimeError("db gone"))):
            await audit.log_read(_Ctx(), PlanningRead(kind="state"), ask=None, draft=None)


class TestLogStaged(unittest.IsolatedAsyncioTestCase):
    async def test_it_names_where_each_held_value_would_go(self):
        rec = AsyncMock()
        with patch(_RECORD, rec):
            await audit.log_staged(
                _Ctx(),
                fields={
                    "annual_income": {
                        "value": 3_600_000,
                        "verb": "adjust",
                        "basis": "20% increase on the ₹30,00,000 on file",
                        "confidence": 0.92,
                        "source": "chat_relative",
                    }
                },
                deletes=[],
            )
        payload = rec.await_args.kwargs["output_payload"]
        self.assertEqual(
            payload["held_fields"][0]["would_write"],
            "personal_finance_profiles.annual_income",
        )
        # The whole point of a staged row: it says nothing was written.
        self.assertFalse(payload["written"])

    async def test_nothing_held_writes_no_row(self):
        rec = AsyncMock()
        with patch(_RECORD, rec):
            await audit.log_staged(_Ctx(), fields={}, deletes=[])
        rec.assert_not_awaited()


class TestLogWrite(unittest.IsolatedAsyncioTestCase):
    async def test_it_records_previous_and_new_per_column(self):
        report = FireReport(
            outcomes=(
                EffectOutcome(
                    name="effective_risk",
                    ran=True,
                    triggered_by=("personal_finance_profiles.annual_income",),
                ),
                EffectOutcome(
                    name="cashflow_plan_cache",
                    ran=False,
                    skipped_reason="nothing written touched the goals table",
                ),
            )
        )
        rec = AsyncMock()
        with patch(_RECORD, rec):
            await audit.log_write(
                _Ctx(), writes=[_Write()], report=report, projection_queued=True
            )
        kw = rec.await_args.kwargs
        changed = kw["output_payload"]["changed"][0]
        self.assertEqual(changed["table"], "personal_finance_profiles")
        self.assertEqual(changed["previous"], 3_000_000)
        self.assertEqual(changed["new"], 3_600_000)
        # The undo target is on the row, so a reversal never has to be guessed.
        self.assertTrue(changed["undo_write_id"])
        # Both halves of the effect decision are on the record.
        self.assertEqual(kw["extra"]["effects_fired"], ["effective_risk"])
        self.assertEqual(kw["extra"]["effects_skipped"], ["cashflow_plan_cache"])
        self.assertEqual(kw["extra"]["tables_touched"], ["personal_finance_profiles"])

    async def test_the_payload_is_json_safe(self):
        # It lands in JSONB, and profile columns hand back dates and Decimals.
        from datetime import date
        from decimal import Decimal

        rec = AsyncMock()
        with patch(_RECORD, rec):
            await audit.log_write(
                _Ctx(),
                writes=[
                    _Write(
                        table_name="users",
                        column_name="date_of_birth",
                        field_key="date_of_birth",
                        previous_value=None,
                        new_value=date(1985, 3, 12),
                        confidence=Decimal("0.910"),
                    )
                ],
                report=FireReport(),
                projection_queued=False,
            )
        json.dumps(rec.await_args.kwargs["output_payload"])


class TestLogUndo(unittest.IsolatedAsyncioTestCase):
    async def test_it_reads_in_the_direction_the_undo_moved_it(self):
        rec = AsyncMock()
        report = FireReport(
            outcomes=(
                EffectOutcome(
                    name="cashflow_plan_cache",
                    ran=True,
                    triggered_by=("personal_finance_profiles.annual_income",),
                ),
            )
        )
        with patch(_RECORD, rec):
            await audit.log_undo(
                None,
                _Ctx.effective_user_id,
                _Ctx.session_id,
                write=_Write(),
                report=report,
            )
        payload = rec.await_args.kwargs["output_payload"]
        self.assertEqual(rec.await_args.kwargs["reason"], "undo")
        # Undoing went from the new value back to the previous one.
        self.assertEqual(payload["from"], 3_600_000)
        self.assertEqual(payload["to"], 3_000_000)
        self.assertEqual(payload["effects"][0]["effect"], "cashflow_plan_cache")


class TestChangeDescription(unittest.TestCase):
    def test_the_log_line_is_readable_for_both_kinds_of_change(self):
        self.assertEqual(
            Change(
                table="risk_profiles",
                column="drop_reaction",
                field_key="drop_reaction",
            ).describe(),
            "risk_profiles.drop_reaction",
        )
        self.assertEqual(
            Change(table="goals", column="*", label="Thar 4x4").describe(),
            "goals[Thar 4x4]",
        )


if __name__ == "__main__":
    unittest.main()
