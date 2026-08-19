"""Who owns the turn.

The precedence order is the whole point of merging the two gates: an open
thread beats the classifier, because the fragments in the middle of a planning
conversation ("50 lakhs down", "yes add it", "no, everything's the same") do not
classify as planning on their own and never will.
"""

from __future__ import annotations

import unittest
import uuid
from unittest.mock import AsyncMock, patch

from app.domains.ai_engine import planning_gate

_STATE = "app.domains.financial_planning.services.planning_state"


class _Ask:
    field_key = "annual_income"
    resume_intent = "asset_allocation"


class _Draft:
    stage = "collecting"


def _patched(*, pending=None, draft=None, asks=0, blocking=None):
    """Patch the state module the gate reaches for (its import is lazy)."""
    return patch.multiple(
        _STATE,
        get_pending=AsyncMock(return_value=pending),
        get_open_draft=AsyncMock(return_value=draft),
        asks_this_session=AsyncMock(return_value=asks),
        deferred_field_keys=AsyncMock(return_value=set()),
        hard_declined_keys=AsyncMock(return_value=set()),
        MAX_ASKS_PER_SESSION=3,
    )


class TestEvaluate(unittest.IsolatedAsyncioTestCase):
    async def _evaluate(self, intent, **kw):
        with _patched(**kw):
            return await planning_gate.evaluate(
                AsyncMock(), uuid.uuid4(), uuid.uuid4(), intent
            )

    async def test_missing_ids_are_a_no_op(self):
        self.assertIsNone(
            await planning_gate.evaluate(None, None, None, "financial_planning")
        )

    async def test_an_open_question_outranks_the_classifier(self):
        # The customer answered "about 32 lakh"; the classifier will call that
        # anything at all. The row knows better.
        d = await self._evaluate("additional_investment", pending=_Ask())
        self.assertIsNotNone(d)
        self.assertTrue(d.routes_to_planning)
        self.assertIsInstance(d.pending_ask, _Ask)
        self.assertEqual(d.resume_intent, "asset_allocation")

    async def test_an_open_goal_draft_outranks_the_classifier(self):
        d = await self._evaluate("additional_investment", draft=_Draft())
        self.assertTrue(d.routes_to_planning)
        self.assertIsInstance(d.draft, _Draft)

    async def test_a_read_only_intent_may_interrupt_an_open_thread(self):
        # "What do I hold?" mid-goal gets answered; the draft is still there.
        d = await self._evaluate("portfolio_query", draft=_Draft())
        self.assertIsNone(d)

    async def test_out_of_scope_does_not_interrupt_an_open_question(self):
        # An unanchored fragment ("50 lakhs down") is exactly what gets labelled
        # out_of_scope. The module decides that AFTER reading it, with the
        # thread in view — the label alone must not steal the answer.
        d = await self._evaluate("out_of_scope", pending=_Ask())
        self.assertTrue(d.routes_to_planning)

    async def test_the_classifier_can_claim_the_turn_itself(self):
        d = await self._evaluate("financial_planning")
        self.assertTrue(d.routes_to_planning)
        self.assertTrue(d.claimed_by_intent)
        self.assertIsNone(d.field_key)

    async def test_a_never_gated_intent_is_left_alone(self):
        for intent in ("portfolio_query", "mutual_fund_query", "general_market_query"):
            with self.subTest(intent=intent):
                self.assertIsNone(await self._evaluate(intent))

    async def test_a_blocked_engine_spends_the_turn_asking(self):
        with _patched():
            with patch.object(
                planning_gate,
                "next_blocking_field",
                AsyncMock(return_value="drop_reaction"),
            ):
                d = await planning_gate.evaluate(
                    AsyncMock(), uuid.uuid4(), uuid.uuid4(), "asset_allocation"
                )
        self.assertEqual(d.field_key, "drop_reaction")
        self.assertEqual(d.resume_intent, "asset_allocation")

    async def test_an_unblocked_engine_runs_as_before(self):
        with _patched():
            with patch.object(
                planning_gate, "next_blocking_field", AsyncMock(return_value=None)
            ):
                d = await planning_gate.evaluate(
                    AsyncMock(), uuid.uuid4(), uuid.uuid4(), "asset_allocation"
                )
        self.assertIsNone(d)

    async def test_it_fails_open(self):
        # Wrongly telling a customer we are missing something we have is worse
        # than letting the engine answer.
        with patch.multiple(
            _STATE, get_pending=AsyncMock(side_effect=RuntimeError("db down"))
        ):
            d = await planning_gate.evaluate(
                AsyncMock(), uuid.uuid4(), uuid.uuid4(), "asset_allocation"
            )
        self.assertIsNone(d)


class TestNextBlockingField(unittest.IsolatedAsyncioTestCase):
    async def test_the_ask_budget_stops_the_nagging(self):
        with _patched(asks=3):
            got = await planning_gate.next_blocking_field(
                AsyncMock(), uuid.uuid4(), uuid.uuid4(), "asset_allocation"
            )
        self.assertIsNone(got)

    async def test_a_declined_field_is_not_re_asked(self):
        # The customer already said no to this one, and it is the only thing
        # blocking. An honest partial answer beats asking again.
        with patch.multiple(
            _STATE,
            asks_this_session=AsyncMock(return_value=0),
            deferred_field_keys=AsyncMock(return_value={"drop_reaction"}),
            hard_declined_keys=AsyncMock(return_value=set()),
            MAX_ASKS_PER_SESSION=3,
        ), patch.object(
            planning_gate, "load_snapshot", AsyncMock(return_value=object())
        ), patch.object(
            planning_gate, "gaps_for_intent", lambda *_: _Gaps(["drop_reaction"])
        ):
            got = await planning_gate.next_blocking_field(
                AsyncMock(), uuid.uuid4(), uuid.uuid4(), "asset_allocation"
            )
        self.assertIsNone(got)

    async def test_the_highest_priority_missing_field_is_the_one_asked(self):
        with patch.multiple(
            _STATE,
            asks_this_session=AsyncMock(return_value=0),
            deferred_field_keys=AsyncMock(return_value=set()),
            hard_declined_keys=AsyncMock(return_value=set()),
            MAX_ASKS_PER_SESSION=3,
        ), patch.object(
            planning_gate, "load_snapshot", AsyncMock(return_value=object())
        ), patch.object(
            planning_gate,
            "gaps_for_intent",
            # date_of_birth (priority 15) sorts ahead of drop_reaction (54).
            lambda *_: _Gaps(["drop_reaction", "date_of_birth"]),
        ):
            got = await planning_gate.next_blocking_field(
                AsyncMock(), uuid.uuid4(), uuid.uuid4(), "asset_allocation"
            )
        self.assertEqual(got, "date_of_birth")


class _Gaps:
    """Stand-in for ``IntentGaps`` — only ``hard_missing`` is read here."""

    def __init__(self, hard):
        self.hard_missing = hard


if __name__ == "__main__":
    unittest.main()
