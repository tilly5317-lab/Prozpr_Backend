"""Drift guard + resolution tests for the ``response_extractor`` agent.

Two failure modes, both silent:

  1. **Schema drift.** The agent constrains every vocabulary to a ``Literal``
     so the Anthropic tool schema enforces it at the API level — the model
     physically cannot emit an unknown verb. Add a member to an enum and forget
     the Literal, and that member becomes un-emittable forever.
  2. **Resolution drift.** The agent reports figures in PARTS and the gateway
     multiplies. If the two stop agreeing on the shape, every number is wrong
     and nothing raises.

Both are checked here without an LLM: the agent's own pydantic models are
constructed directly and pushed through the real resolver.
"""

from __future__ import annotations

import unittest
from typing import get_args

from app.domains.ai_engine.common import ensure_ai_agents_path
from app.domains.financial_planning.services.planning_extractor import resolve

ensure_ai_agents_path()

from response_extractor import (  # type: ignore[import-not-found]  # noqa: E402
    Change,
    ExtractedOperation,
    ExtractionResult,
    GoalSlots,
    Money,
)
from response_extractor import models as m  # noqa: E402
from response_extractor import extractor as x  # noqa: E402


class TestSchemaDrift(unittest.TestCase):
    """Every Literal in the tool schema must list every member of its enum."""

    CASES = (
        ("_TargetLiteral", m.Target),
        ("_VerbLiteral", m.Verb),
        ("_MagnitudeLiteral", m.Magnitude),
        ("_PeriodLiteral", m.Period),
        ("_DirectionLiteral", m.Direction),
        ("_MessageKindLiteral", m.MessageKind),
        ("_GoalTypeLiteral", m.GoalType),
    )

    def test_literals_match_their_enums(self):
        for literal_name, enum in self.CASES:
            with self.subTest(literal=literal_name):
                literal = getattr(x, literal_name)
                self.assertEqual(
                    set(get_args(literal)),
                    {member.value for member in enum},
                    f"{literal_name} has drifted from {enum.__name__}",
                )


class TestAgentStaysOutOfTheApp(unittest.TestCase):
    def test_the_agent_imports_nothing_from_app(self):
        """An agent under ``src/`` cannot import ``app`` — it is loaded by bare
        module name via sys.path injection, and app imports it, not the reverse."""
        import pathlib

        pkg = pathlib.Path(x.__file__).parent
        for path in pkg.glob("*.py"):
            source = path.read_text(encoding="utf-8")
            with self.subTest(file=path.name):
                self.assertNotIn("from app.", source)
                self.assertNotIn("import app.", source)

    def test_the_agent_is_never_told_where_a_field_is_stored(self):
        """``CapturableField`` carries no table or column, on purpose: the write
        router owns that mapping and the agent has no business knowing it."""
        fields = set(m.CapturableField.model_fields)
        self.assertNotIn("table", fields)
        self.assertNotIn("column", fields)

    def test_the_extraction_input_carries_no_stored_values(self):
        """The whole privacy story: the agent is told which fields EXIST, never
        what the customer's figures are."""
        fields = set(m.ExtractionInput.model_fields)
        self.assertEqual(
            fields,
            {
                "utterance",
                "capturable_fields",
                "asked_field_key",
                "awaiting",
                "goal_names_on_file",
                "draft_summary",
                "history",
            },
        )


def _result(*operations, kind="state", **kw) -> ExtractionResult:
    return ExtractionResult(kind=kind, operations=list(operations), **kw)


class TestResolution(unittest.TestCase):
    """The agent's parts, multiplied out by the gateway."""

    def test_a_monthly_figure_against_a_yearly_field_is_annualised(self):
        read = resolve(
            _result(
                ExtractedOperation(
                    target="profile",
                    verb="set",
                    field_key="annual_income",
                    value=Money(amount=2.4, magnitude="lakh", period="per_month"),
                    confidence=0.95,
                )
            ),
            {},
        )
        self.assertEqual(read.operations[0].value, 2_880_000)
        self.assertEqual(read.operations[0].verb, "set")

    def test_a_relative_change_resolves_against_the_stored_value(self):
        read = resolve(
            _result(
                ExtractedOperation(
                    target="profile",
                    verb="adjust",
                    field_key="annual_income",
                    change=Change(direction="increase", pct=20),
                    confidence=0.93,
                )
            ),
            {"annual_income": 3_000_000},
        )
        op = read.operations[0]
        self.assertEqual(op.value, 3_600_000)
        self.assertIn("20%", op.basis)

    def test_a_relative_change_with_no_baseline_becomes_unread(self):
        read = resolve(
            _result(
                ExtractedOperation(
                    target="profile",
                    verb="adjust",
                    field_key="annual_income",
                    change=Change(direction="increase", pct=20),
                    confidence=0.93,
                )
            ),
            {},
        )
        self.assertEqual(read.operations, [])
        self.assertEqual(read.unread[0].reason, "no_baseline")

    def test_a_missing_period_becomes_a_clarifying_question(self):
        read = resolve(
            _result(
                ExtractedOperation(
                    target="profile",
                    verb="set",
                    field_key="annual_income",
                    value=Money(amount=2.4, magnitude=None, period="none"),
                    confidence=0.9,
                )
            ),
            {},
        )
        self.assertEqual(read.unread[0].reason, "ambiguous_unit")
        self.assertEqual(read.ambiguous_field_key, "annual_income")
        self.assertIn("per month or per year", read.clarification)

    def test_a_low_confidence_value_is_held_back_from_writing(self):
        read = resolve(
            _result(
                ExtractedOperation(
                    target="profile",
                    verb="set",
                    field_key="annual_income",
                    value=Money(amount=32, magnitude="lakh", period="per_year"),
                    confidence=0.55,
                )
            ),
            {},
        )
        self.assertEqual(read.operations, [])
        self.assertEqual(read.unread[0].reason, "low_confidence")

    def test_goal_money_is_scaled_and_attributed(self):
        read = resolve(
            _result(
                ExtractedOperation(
                    target="goal",
                    verb="create",
                    goal=GoalSlots(
                        goal_name="Thar 4x4",
                        goal_type="VEHICLE",
                        years=3,
                        cost=Money(amount=18, magnitude="lakh"),
                    ),
                    confidence=0.9,
                )
            ),
            {},
        )
        slots = read.operations[0].slots
        self.assertEqual(slots["cost_pv"], 1_800_000)
        # Whose number it is decides how the reply describes it.
        self.assertEqual(slots["cost_source"], "customer")
        self.assertEqual(slots["goal_type"], "VEHICLE")

    def test_an_agent_estimate_is_marked_as_ours(self):
        read = resolve(
            _result(
                ExtractedOperation(
                    target="goal",
                    verb="create",
                    goal=GoalSlots(
                        goal_name="BMW X5",
                        cost_estimate=Money(amount=1, magnitude="crore"),
                    ),
                    confidence=0.8,
                )
            ),
            {},
        )
        slots = read.operations[0].slots
        self.assertEqual(slots["cost_pv"], 10_000_000)
        self.assertEqual(slots["cost_source"], "assistant_estimate")

    def test_an_invented_field_key_is_dropped(self):
        # The registry is the authority, not the model.
        read = resolve(
            _result(
                ExtractedOperation(
                    target="profile",
                    verb="set",
                    field_key="favourite_colour",
                    text_value="blue",
                    confidence=0.99,
                )
            ),
            {},
        )
        self.assertEqual(read.operations, [])

    def test_one_message_can_carry_a_write_and_a_projection(self):
        read = resolve(
            _result(
                ExtractedOperation(
                    target="profile",
                    verb="set",
                    field_key="annual_income",
                    value=Money(amount=32, magnitude="lakh", period="per_year"),
                    confidence=0.95,
                ),
                ExtractedOperation(target="plan", verb="project", confidence=0.9),
            ),
            {},
        )
        self.assertEqual(len(read.operations), 2)
        self.assertTrue(read.wants_projection)

    def test_unchanged_fields_are_filtered_to_the_registry(self):
        read = resolve(
            _result(
                kind="state",
                unchanged_fields=["monthly_household_expense", "not_a_field"],
            ),
            {},
        )
        self.assertEqual(read.unchanged, ["monthly_household_expense"])


if __name__ == "__main__":
    unittest.main()
