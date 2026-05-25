"""TEMP(rebalancing-chat) — delete this module when rebalancing chat ships.

Touchpoints to remove or revert together:
  - ``allocation_only_stub.py`` — allocation-only handler
  - ``_temp_allocation_tables_md.py`` — deterministic table markdown (no LLM)
  - ``chat_core/brain.py`` — rebalancing branch that imports the stub
  - ``intent_classifier_service.py`` — ``_apply_rebalancing_keyword_override``
  - ``config.Settings.rebalancing_engine_enabled()`` — env gate (default off)
  - ``tests/test_rebalancing_intent_override.py``

Enable full rebalancing chat: set ``REBALANCING_ENGINE_ENABLED=true`` and flip
the flags below to ``False``, then delete this file and the stub.
"""

# TEMP: keyword fix when LLM mislabels "rebalance my portfolio" as asset_allocation.
TEMP_REBALANCING_INTENT_KEYWORD_OVERRIDE = True

# TEMP: run goal allocation + ideal snapshot; skip ``run_rebalancing`` in chat.
TEMP_REBALANCING_RUN_ALLOCATION_ONLY = True
