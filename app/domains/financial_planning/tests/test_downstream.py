"""Only what actually changed gets re-run.

The rule the whole module exists to enforce: an effect fires when a column it
depends on was written, and not otherwise. Both halves matter — a missed fire
serves a plan built on a stale input, and a spurious one pays for a re-score
that produces the identical number.
"""

from __future__ import annotations

import unittest
import uuid
from unittest.mock import AsyncMock, patch

from app.domains.financial_planning.services import downstream
from app.domains.financial_planning.services.downstream import Change

# Patched where the effects actually reach for them (both imports are lazy, so
# these are resolved at call time) rather than on ``downstream`` itself — that
# way the test exercises the real call path instead of a stand-in for it.
_RISK = (
    "app.domains.profile.services._effective_risk.maybe_recalculate_effective_risk"
)
_PLAN = "app.domains.cashflow.services.cashflow_persist_service.mark_stale"


def _profile_change(field_key: str, table: str = "personal_finance_profiles") -> Change:
    return Change(table=table, column=field_key, field_key=field_key)


class TestWhatFires(unittest.IsolatedAsyncioTestCase):
    async def _fire(self, changes):
        risk, plan = AsyncMock(), AsyncMock()
        with patch(_RISK, risk), patch(_PLAN, plan):
            report = await downstream.fire(AsyncMock(), uuid.uuid4(), changes)
        return report.fired, risk, plan

    async def test_nothing_changed_runs_nothing(self):
        fired, risk, plan = await self._fire([])
        self.assertEqual(fired, [])
        risk.assert_not_awaited()
        plan.assert_not_awaited()

    async def test_income_is_both_a_risk_input_and_a_plan_input(self):
        fired, risk, plan = await self._fire([_profile_change("annual_income")])
        self.assertEqual(sorted(fired), ["cashflow_plan_cache", "effective_risk"])
        risk.assert_awaited_once()
        plan.assert_awaited_once()

    async def test_a_risk_only_field_does_not_touch_the_plan_cache(self):
        # drop_reaction feeds the score and nothing the projection reads.
        fired, risk, plan = await self._fire(
            [_profile_change("drop_reaction", table="risk_profiles")]
        )
        self.assertEqual(fired, ["effective_risk"])
        risk.assert_awaited_once()
        plan.assert_not_awaited()

    async def test_a_plan_only_field_does_not_re_score_risk(self):
        # starting_monthly_investment is read by the engine but is not a risk input.
        fired, risk, plan = await self._fire(
            [_profile_change("starting_monthly_investment")]
        )
        self.assertEqual(fired, ["cashflow_plan_cache"])
        risk.assert_not_awaited()
        plan.assert_awaited_once()

    async def test_a_field_neither_depends_on_fires_nothing(self):
        fired, risk, plan = await self._fire(
            [_profile_change("emergency_fund_months", table="investment_profiles")]
        )
        self.assertEqual(fired, [])
        risk.assert_not_awaited()
        plan.assert_not_awaited()

    async def test_a_goal_change_retires_the_plan_cache(self):
        fired, risk, plan = await self._fire(
            [Change(table="goals", column="*", field_key=None)]
        )
        self.assertEqual(fired, ["cashflow_plan_cache"])
        plan.assert_awaited_once()
        risk.assert_not_awaited()

    async def test_two_risk_inputs_in_one_turn_re_score_once(self):
        fired, risk, _ = await self._fire(
            [_profile_change("annual_income"), _profile_change("financial_assets")]
        )
        self.assertIn("effective_risk", fired)
        risk.assert_awaited_once()

    async def test_a_failing_effect_does_not_take_the_write_with_it(self):
        risk = AsyncMock(side_effect=RuntimeError("scorer down"))
        plan = AsyncMock()
        with patch(_RISK, risk), patch(_PLAN, plan):
            report = await downstream.fire(
                AsyncMock(), uuid.uuid4(), [_profile_change("annual_income")]
            )
        # The re-score is not reported as fired, but the plan cache still went,
        # and the failure is on the record rather than swallowed silently.
        self.assertEqual(report.fired, ["cashflow_plan_cache"])
        failed = next(o for o in report.outcomes if o.name == "effective_risk")
        self.assertEqual(failed.error, "RuntimeError")
        self.assertIn("FAILED", report.as_line())


class TestTheReportExplainsItself(unittest.IsolatedAsyncioTestCase):
    """The report is the answer to "why did my plan get recomputed?" — so it
    has to name the column, and it has to cover the effects that did NOT run."""

    async def _fire(self, changes):
        with patch(_RISK, AsyncMock()), patch(_PLAN, AsyncMock()):
            return await downstream.fire(AsyncMock(), uuid.uuid4(), changes)

    async def test_it_names_the_column_that_triggered_the_work(self):
        report = await self._fire([_profile_change("annual_income")])
        plan = next(o for o in report.outcomes if o.name == "cashflow_plan_cache")
        self.assertTrue(plan.ran)
        self.assertEqual(
            plan.triggered_by, ("personal_finance_profiles.annual_income",)
        )

    async def test_a_goal_is_named_not_numbered(self):
        # A UUID in a log line is useless to whoever reads it.
        report = await self._fire(
            [Change(table="goals", column="*", field_key=None, label="Thar 4x4")]
        )
        plan = next(o for o in report.outcomes if o.name == "cashflow_plan_cache")
        self.assertEqual(plan.triggered_by, ("goals[Thar 4x4]",))

    async def test_every_effect_is_accounted_for_even_when_skipped(self):
        report = await self._fire(
            [_profile_change("drop_reaction", table="risk_profiles")]
        )
        self.assertEqual({o.name for o in report.outcomes}, {e.name for e in downstream.EFFECTS})
        skipped = next(o for o in report.outcomes if o.name == "cashflow_plan_cache")
        self.assertFalse(skipped.ran)
        self.assertIn("nothing written touched", skipped.skipped_reason)

    async def test_a_turn_that_changed_nothing_says_so(self):
        report = await self._fire([])
        self.assertEqual(report.fired, [])
        self.assertTrue(
            all(o.skipped_reason == "nothing was written this turn" for o in report.outcomes)
        )

    async def test_the_payload_round_trips_for_the_telemetry_row(self):
        import json

        report = await self._fire([_profile_change("annual_income")])
        # It is persisted as JSONB, so it has to be JSON.
        json.dumps(report.as_payload())


class TestProjectionPredicate(unittest.TestCase):
    """The same list decides whether we owe them a fresh verdict this turn."""

    def test_plan_input(self):
        self.assertTrue(
            downstream.plan_inputs_changed([_profile_change("annual_income")])
        )

    def test_goal(self):
        self.assertTrue(
            downstream.plan_inputs_changed(
                [Change(table="goals", column="*", field_key=None)]
            )
        )

    def test_neither(self):
        self.assertFalse(
            downstream.plan_inputs_changed(
                [_profile_change("drop_reaction", table="risk_profiles")]
            )
        )


class TestAuditRowAdapter(unittest.TestCase):
    def test_goal_rows_carry_no_field_key_but_do_carry_a_name(self):
        class _Row:
            table_name = "goals"
            column_name = "*"
            field_key = "8f14e45f-ea1c-4a02-9b28-000000000000"
            new_value = {"name": "Thar 4x4"}
            previous_value = None

        changes = downstream.changes_from_writes([_Row()])
        self.assertEqual(changes[0].table, "goals")
        # The goal id is not a registry key and must never be matched as one.
        self.assertIsNone(changes[0].field_key)
        # But the log line has to be readable, so the name comes along.
        self.assertEqual(changes[0].label, "Thar 4x4")
        self.assertEqual(changes[0].describe(), "goals[Thar 4x4]")

    def test_a_deleted_goal_takes_its_name_from_the_previous_value(self):
        class _Row:
            table_name = "goals"
            column_name = "*"
            field_key = "8f14e45f-ea1c-4a02-9b28-000000000000"
            new_value = None
            previous_value = {"name": "Europe trip"}

        self.assertEqual(
            downstream.changes_from_writes([_Row()])[0].describe(), "goals[Europe trip]"
        )

    def test_a_profile_row_describes_as_table_dot_column(self):
        class _Row:
            table_name = "personal_finance_profiles"
            column_name = "annual_income"
            field_key = "annual_income"
            new_value = 3600000
            previous_value = 3000000

        change = downstream.changes_from_writes([_Row()])[0]
        self.assertEqual(change.describe(), "personal_finance_profiles.annual_income")


if __name__ == "__main__":
    unittest.main()
