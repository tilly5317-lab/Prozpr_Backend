"""Per-intent bridges.

Each ``*_bridge.py`` module exposes::

    async def run(ctx, *, turn, flow, intent) -> BranchResult

The intent_router imports these bridge modules by their submodule path; this
``__init__.py`` deliberately does NOT eagerly import them — doing so creates a
cycle with the per-intent service modules (``general_chat_service``,
``market_commentary_service``, ``intent_classifier_service``,
``portfolio_query_service``) that the bridges depend on.

Bridges are the only place that knows about the underlying AI_Agents-call
modules; the orchestrator and the dispatch table are agnostic to that detail.
"""
