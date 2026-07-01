"""flow_additional_investment: PAA first, then deploy fresh money into funds.

Mirrors the flow_rebalancing recipe documented in flow.py: run the practical
(holdings-aware) asset allocation first, hand it forward via
prior[AIModule.ASSET_ALLOCATION.value], and let the additional_investment
domain own the final reply. The flow's domain imports are lazy (function-local),
so we inject fake module-service modules into sys.modules instead of patching a
real symbol — that keeps this a pure wiring unit test with no DB/LLM and no
dependency on the additional_investment domain being importable yet.
"""

from __future__ import annotations

import sys
import types
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.domains.ai_engine.services.flow import FLOWS, flow_additional_investment
from app.domains.ai_engine.types import ModuleOutput

_PAA_MODULE = (
    "app.domains.practical_asset_allocation.services."
    "practical_asset_allocation_module_service"
)
_AINV_MODULE = (
    "app.domains.additional_investment.services."
    "additional_investment_module_service"
)


class AdditionalInvestmentFlowTests(unittest.IsolatedAsyncioTestCase):
    def test_flows_row_points_at_flow_additional_investment(self):
        self.assertIs(FLOWS["additional_investment"], flow_additional_investment)

    async def test_flow_calls_additional_investment_directly(self):
        """The additional_investment orchestrator self-primes PAA, so the flow
        calls the domain directly with an empty prior — it does NOT pre-run PAA
        (which would compute the practical allocation twice per turn)."""
        turn = MagicMock(name="turn")
        ctx = MagicMock(name="ctx")

        ainv_output = ModuleOutput(text="Buy fund X with your fresh deploy amount.")

        paa_run = AsyncMock()
        ainv_run = AsyncMock(return_value=ainv_output)

        fake_paa_mod = types.ModuleType(_PAA_MODULE)
        fake_paa_mod.run = paa_run
        fake_ainv_mod = types.ModuleType(_AINV_MODULE)
        fake_ainv_mod.run = ainv_run

        with patch.dict(
            sys.modules,
            {_PAA_MODULE: fake_paa_mod, _AINV_MODULE: fake_ainv_mod},
        ):
            result = await flow_additional_investment(turn, ctx)

        # additional_investment owns the reply — returned unchanged.
        self.assertIs(result, ainv_output)
        # Called directly with an empty prior; PAA is NOT pre-run by the flow.
        ainv_run.assert_awaited_once_with(turn, ctx, {})
        paa_run.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
