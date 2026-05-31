"""Chat bridge for the cashflow_statement (goal-planning) agent.

Bridges ``AI_Agents/src/cashflow_statement`` (engine + summarizer) into chat.
The bridge maps ORM User data to ``GoalPlanningInput``, runs the cashflow
engine, builds a ``facts_pack``, and hands it to the shared answer-formatter.

Modules:
- ``input_builder.py`` — ORM User → GoalPlanningInput mapping + default assumptions seeding.
- ``service.py`` — engine execution, persistence, facts_pack construction.
- ``chat.py`` — @register("goal_planning") chat handler.
"""
