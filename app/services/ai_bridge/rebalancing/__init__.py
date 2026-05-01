"""Rebalancing domain — engine adapter, chat handler, and input builder.

Public surface re-exports the bridge entry points. The ``chat`` submodule is
**not** auto-imported here: doing so triggers a circular import via
``chat_core.turn_context``. Callers that need its ``@register`` side-effect
must import ``chat`` lazily (e.g. inside a function body in ``chat_core/brain.py``).

NOTE: ``service.compute_rebalancing_result`` and ``RebalancingRunOutcome`` will
be added in Task 9. Until then this module exposes only the fund_rank loader.
"""
