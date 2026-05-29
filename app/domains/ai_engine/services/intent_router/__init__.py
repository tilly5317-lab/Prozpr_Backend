"""Shared classifier-LLM helpers.

The old ``intent_router.dispatch`` single-step switch was replaced by the
sequence-of-modules pattern in ``chat_orchestrator.brain`` (``_SEQUENCES``).
This package now holds only ``classifier_llm.classify_action``, the shared
Haiku helper that per-module follow-up classifiers (asset_allocation/chat,
rebalancing/chat, …) reuse for their own follow-up intent detection.
"""
