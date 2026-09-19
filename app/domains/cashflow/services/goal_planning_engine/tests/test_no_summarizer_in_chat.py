"""The chat path does not pre-narrate the plan before the formatter narrates it.

``summarize_plan`` is a second Haiku call that turns engine JSON into narrative
bullets. It predates the shared answer formatter — its docstring still describes
itself as "the handoff payload a customer-facing LLM ... can consume directly",
which is the formatter's job now. In chat its output only ever reached
``facts_pack["narrative"]``, so a narrator was writing prose for a narrator, on
every turn, serially. It was also built with ``question_aware=False``, so that
prose pulled against the formatter's instruction to answer the question asked.

It is NOT dead code: the onboarding, goals and profile routers run the engine and
persist ``CashflowPlanSummary`` for the Goal Planning screen, which has no
formatter. Those callers keep it. Only the chat path drops it.
"""

from __future__ import annotations

import inspect

from app.domains.cashflow.services.goal_planning_engine import service


def test_chat_service_does_not_import_the_summarizer():
    """Asserts on the import, not the name — the comment explaining why the call
    was removed legitimately mentions ``summarize_plan``."""
    source = inspect.getsource(service)

    assert "from cashflow_statement.summarizer import" not in source, (
        "the chat path must not pre-narrate — the formatter writes the reply"
    )


def test_facts_pack_and_fallback_survive_without_a_narrative():
    """Both already handled summary=None; that is now the only path."""
    facts_sig = inspect.signature(service._build_facts_pack)
    fallback_sig = inspect.signature(service._build_fallback_text)

    assert "summary" not in facts_sig.parameters
    assert "summary" not in fallback_sig.parameters
