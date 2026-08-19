"""The projection must run on what the turn just wrote.

The engine reads the ORM graph loaded at the START of the turn. That is fine for
a profile column — the write mutates the very object the graph holds — but not
for a goal: ``db.add`` never appends to an already-loaded collection and
``db.delete`` never removes from one. Left alone, adding a goal in chat produced
a feasibility verdict computed WITHOUT that goal, which is the one thing the
customer was asking about.
"""

from __future__ import annotations

import unittest
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

# Registers every ORM model so SQLAlchemy can configure its mappers — the
# loader's statement references relationships across several domains.
import app.all_models  # noqa: F401
from app.domains.ai_engine.chat_types import ChatTurnInput
from app.domains.ai_engine.services import flow
from app.domains.ai_engine.turn_context import TurnContext
from app.domains.ai_engine.types import ModuleOutput

_LOADER = "app.domains.identity.services.user_context_loader.load_user_for_ai"


def _turn_and_ctx(db=None):
    """Real dataclasses, because the code under test uses dataclasses.replace."""
    user = MagicMock(name="stale-graph")
    session_id, user_id = uuid.uuid4(), uuid.uuid4()
    db = db if db is not None else MagicMock()
    turn = ChatTurnInput(
        user_ctx=user,
        user_question="am I on track?",
        conversation_history=[],
        client_context=None,
        session_id=session_id,
        db=db,
        user_id=user_id,
    )
    ctx = TurnContext(
        user_ctx=user,
        user_question=turn.user_question,
        conversation_history=[],
        client_context=None,
        session_id=session_id,
        db=db,
        effective_user_id=user_id,
        last_agent_runs={},
        active_intent=None,
    )
    return turn, ctx


class TestRefreshBeforeProjecting(unittest.IsolatedAsyncioTestCase):
    async def _run(self, side, loader=None):
        turn, ctx = _turn_and_ctx()
        loader = loader or AsyncMock(return_value=MagicMock(name="fresh-graph"))
        with patch(_LOADER, loader):
            out_turn, out_ctx = await flow._refresh_user_graph(turn, ctx, side)
        return out_turn, out_ctx, ctx, loader

    async def test_a_new_goal_forces_a_refresh(self):
        _, out_ctx, original, loader = await self._run({"goal_saved": {"goal": "Thar"}})
        loader.assert_awaited_once()
        # populate_existing is the whole point: without it the identity-mapped
        # user comes back with its stale financial_goals collection intact.
        self.assertIs(loader.await_args.kwargs["refresh"], True)
        self.assertIsNot(out_ctx.user_ctx, original.user_ctx)

    async def test_a_removed_goal_forces_a_refresh(self):
        _, _, _, loader = await self._run({"goal_removed": [{"goal": "Europe trip"}]})
        loader.assert_awaited_once()

    async def test_a_written_profile_field_forces_a_refresh(self):
        _, _, _, loader = await self._run(
            {"planning_saved": [{"field_key": "annual_income"}]}
        )
        loader.assert_awaited_once()

    async def test_a_turn_that_wrote_nothing_does_not_re_query(self):
        # The common case — "am I on track?" with nothing to change. Re-reading
        # the whole graph for an unchanged projection is pure cost.
        _, out_ctx, original, loader = await self._run({"run_projection": True})
        loader.assert_not_awaited()
        self.assertIs(out_ctx, original)

    async def test_values_only_staged_do_not_count_as_written(self):
        # planning_noted means held, not saved. Nothing changed in the database,
        # so there is nothing to re-read.
        _, _, _, loader = await self._run(
            {"planning_noted": [{"field_key": "annual_income"}]}
        )
        loader.assert_not_awaited()

    async def test_a_failed_refresh_still_projects(self):
        # Better a projection on the preloaded graph than no answer at all.
        loader = AsyncMock(side_effect=RuntimeError("db gone"))
        _, out_ctx, original, _ = await self._run({"goal_saved": {"goal": "x"}}, loader)
        self.assertIs(out_ctx, original)

    async def test_a_missing_user_is_survived(self):
        loader = AsyncMock(return_value=None)
        _, out_ctx, original, _ = await self._run({"goal_saved": {"goal": "x"}}, loader)
        self.assertIs(out_ctx, original)


class TestLoaderRefreshFlag(unittest.IsolatedAsyncioTestCase):
    """``refresh=True`` must reach the query as populate_existing."""

    async def _capture(self, **kw):
        from app.domains.identity.services import user_context_loader as loader

        db = MagicMock()
        db.execute = AsyncMock(
            return_value=MagicMock(scalar_one_or_none=lambda: MagicMock())
        )
        await loader.load_user_for_ai(db, uuid.uuid4(), **kw)
        return db.execute.await_args.args[0]

    async def test_off_by_default(self):
        stmt = await self._capture()
        self.assertNotIn("populate_existing", stmt.get_execution_options())

    async def test_on_when_asked(self):
        stmt = await self._capture(refresh=True)
        self.assertTrue(stmt.get_execution_options().get("populate_existing"))


class TestProjectStillRuns(unittest.IsolatedAsyncioTestCase):
    """The refresh sits inside _project and must not change what it returns."""

    async def test_the_projection_output_is_returned_with_chips_carried(self):
        projection = ModuleOutput(text="you're on track", side_effects={})
        with patch.object(
            flow, "_refresh_user_graph", AsyncMock(side_effect=lambda t, c, s: (t, c))
        ), patch(
            "app.domains.cashflow.services.cashflow_module_service.run",
            AsyncMock(return_value=projection),
        ), patch(
            "app.domains.ai_engine.planning_gate.next_blocking_field",
            AsyncMock(return_value=None),
        ):
            turn, ctx = _turn_and_ctx()
            out = await flow._project(
                turn, ctx, {"planning_saved": [{"field_key": "annual_income"}]}
            )
        self.assertEqual(out.text, "you're on track")
        # The saved chip has to survive the handover, or the write happens invisibly.
        self.assertIn("planning_saved", out.side_effects)


if __name__ == "__main__":
    unittest.main()
