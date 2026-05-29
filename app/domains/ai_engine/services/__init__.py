"""ai_engine services — chat orchestrator, intent router, bridges, formatter.

Layout::

    services/
        types.py                  # BranchResult + IntentDecision
        chat_orchestrator/        # ChatBrain (the "brain" loop)
        intent_router/            # the _DISPATCH switch
        bridges/                  # one file per intent + intent_classifier_bridge
"""
