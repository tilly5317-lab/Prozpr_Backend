"""financial_planning — the customer's plan, and the facts behind it.

One domain for what used to be two intents. It owns everything the customer can
say about their own financial position: stating or relatively adjusting a
figure, creating / editing / removing a goal, reading any of it back, and
asking what the plan does with it.

Public entry: ``services.planning_module_service.run(turn, ctx, prior)``, called
by ``ai_engine``'s ``flow_financial_planning``. Everything else here is internal
to the domain.

The load-bearing pieces, in the order a turn touches them:

    planning_extractor  one message  -> typed operations (the AI module)
    operations          the arithmetic the model is never asked to do
    profile_ops         CRUD on plan inputs, staged then written
    goal_ops            CRUD on goals, every verb undoable
    goal_builder        the multi-turn conversation that costs a goal
    downstream          what to re-run, keyed on what actually changed
    privacy             what may reach a model, and in what shape
"""
