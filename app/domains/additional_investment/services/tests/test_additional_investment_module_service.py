"""additional_investment_module_service.run: wraps the chat handler result into a ModuleOutput.

Mirrors the rebalancing/cashflow module-service contract: lazy-import the chat
module for its @register side-effect, dispatch on the intent, wrap the
ChatHandlerResult into a ModuleOutput. Uses a plain in-registry fake handler
(no real LLM, no DB) and stubs the ainv_engine.chat import via sys.modules so
this task is testable before chat.py exists.
"""

from __future__ import annotations

import asyncio
import sys
import types as _types
import unittest
import uuid
from unittest.mock import MagicMock

from app.domains.ai_engine import chat_dispatcher as cd
from app.domains.ai_engine.chat_dispatcher import ChatHandlerResult
from app.domains.ai_engine.types import ModuleOutput

_AINV_PKG = "app.domains.additional_investment.services.ainv_engine"
_AINV_CHAT = _AINV_PKG + ".chat"


class AdditionalInvestmentModuleServiceTests(unittest.TestCase):
    def setUp(self):
        # run() lazy-imports ainv_engine.chat for its @register side-effect.
        # chat.py is a later task and is LLM-backed, so stub the package +
        # submodule in sys.modules. We register our own fake handler below, so
        # the real registration side-effect is irrelevant to this unit test.
        self._saved = {name: sys.modules.get(name) for name in (_AINV_PKG, _AINV_CHAT)}
        fake_pkg = _types.ModuleType(_AINV_PKG)
        fake_chat = _types.ModuleType(_AINV_CHAT)
        fake_pkg.chat = fake_chat
        sys.modules[_AINV_PKG] = fake_pkg
        sys.modules[_AINV_CHAT] = fake_chat
        cd._HANDLERS.pop("additional_investment", None)

    def tearDown(self):
        for name, mod in self._saved.items():
            if mod is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = mod
        cd._HANDLERS.pop("additional_investment", None)

    def test_run_wraps_chat_result_into_module_output(self):
        from app.domains.additional_investment.services import (
            additional_investment_module_service as svc,
        )

        snap = uuid.uuid4()
        charts = [{"kind": "fund_buys", "series": []}]
        fake_result = ChatHandlerResult(
            text="Deploy ₹1,00,000: buy Fund A (₹60,000) and Fund B (₹40,000).",
            snapshot_id=snap,
            chart_payloads=charts,
        )

        captured = {}

        @cd.register("additional_investment")
        async def fake_handler(turn_context):
            captured["ctx"] = turn_context
            return fake_result

        ctx = MagicMock()
        out = asyncio.run(svc.run(MagicMock(), ctx, {}))

        # Routed to the additional_investment handler with the turn's ctx.
        self.assertIs(captured["ctx"], ctx)

        # Wrapped verbatim into a ModuleOutput.
        self.assertIsInstance(out, ModuleOutput)
        self.assertTrue(out.text)  # non-empty
        self.assertEqual(out.text, fake_result.text)
        self.assertIs(out.payload, fake_result)
        # Finding 6: the gateway deliberately does NOT map snapshot_id (the
        # additional-investment engine produces no snapshot), so it stays None
        # even though the handler result carries one.
        self.assertIsNone(out.snapshot_id)
        self.assertEqual(out.chart_payloads, charts)
        # 3a: no AdditionalInvestmentRun persisted yet.
        self.assertIsNone(out.persisted_run_id)


if __name__ == "__main__":
    unittest.main()
