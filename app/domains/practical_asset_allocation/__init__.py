"""practical_asset_allocation domain — holdings-aware goal-based allocation.

Owns the app-layer gateway to ``AI_Agents/src/practical_asset_allocation``
(the holdings-aware variant of asset_allocation). Used as the first step of
the rebalancing flow: it produces the target allocation the rebalancing engine
rebalances towards. See ``services/practical_asset_allocation_module_service.py``.
"""
