"""Additional-investment engine adapter, chat handler, and input builder.

Wraps the pure ``AI_Agents.additional_investment`` engine
(``run_additional_investment``) for the FastAPI app. The ``chat`` submodule is
**not** auto-imported here: doing so triggers a circular import via
``chat_core.turn_context``. Callers that need its ``@register`` side-effect
must import ``chat`` lazily (e.g. inside ``additional_investment_module_service.run``).

NOTE: ``service.compute_additional_investment_result`` /
``AdditionalInvestmentRunOutcome``, ``input_builder``, ``fund_rank`` and
``chat`` are added in later Plan-3a tasks. Until then this package is an
import-only marker. The engine subfolder is named ``ainv_engine`` because
``ai_engine`` is already taken by the chat orchestrator domain.
"""
