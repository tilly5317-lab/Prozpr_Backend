"""Intent classifier domain — the ONLY domain allowed to import
``AI_Agents.intent_classifier``.

Public surface::

    from app.domains.intent_classifier.services.intent_classifier_service import classify

The chat brain calls ``classify(turn, ctx)`` and gets back an
``IntentDecision``. No other code goes near the underlying classifier
orchestrator.
"""
