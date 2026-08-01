"""Regression guard: every migrated customer-facing surface must build its system
prompt from the shared persona (AI_Agents/src/persona.py).

Pure string assertions — no LLM, no DB. Run: .venv-mac/bin/python -m pytest AI_Agents/src/test_persona_surfaces.py
"""

PLAIN_MARKER = "do not use tables"


def _is_plain(s: str) -> bool:
    return PLAIN_MARKER in s.lower()


def test_plain_narrators_use_shared_voice():
    from risk_profiling.prompts import _SYSTEM as RISK
    from asset_allocation_pydantic.steps._rationale_llm import _SYSTEM_PROMPT as RAT
    from cashflow_statement.summarizer import SYSTEM_PROMPT as CF

    for s in (RISK, RAT, CF):
        assert "You are PI" in s
        assert _is_plain(s)  # plain profile (embedded prose)
        assert "restating" not in s.lower()  # generated summaries, not question answers


def test_market_commentary_document_and_qa():
    from market_commentary.prompts import (
        DOCUMENT_GENERATION_SYSTEM_PROMPT as DOC,
        QA_SYSTEM_PROMPT as QA,
        QA_PROMPT,
    )

    assert "You are PI" in DOC and "Certified Financial Planner" not in DOC
    assert "restating" not in DOC.lower()  # document profile, not question-aware
    assert "You are PI" in QA and "restating" in QA.lower()
    assert "{document_content}" in QA  # placeholder preserved
    assert {"document_content", "user_question"} <= set(QA_PROMPT.input_variables)


def test_canned_messages_single_pi_identity():
    from intent_classifier.prompts import (
        OUT_OF_SCOPE_MESSAGE as OOS,
        STOCK_ADVICE_MESSAGE as STK,
    )

    assert OOS.startswith("I'm PI — at Prozpr,")
    assert STK.startswith("I'm PI — at Prozpr,")
    assert "Tilly" not in OOS and "Tilly" not in STK


def test_chat_facing_surfaces_lead_with_the_answer():
    """Restating the question is conditional, not a required opening.

    Every reply beginning "You're asking whether…" reads as filler by the third
    time. The rule now leads with the answer and keeps the restatement for
    questions where naming the reading earns its place.
    """
    from app.domains.general_chat.services.general_chat_engine import (
        _SYSTEM_PROMPT as GC,
    )
    from app.domains.ai_engine.answer_formatter import FORMATTER_HOUSE_STYLE as FMT

    # The AA composer was removed; AA chat now renders through the answer_formatter (FMT).
    for s in (GC, FMT):
        low = s.lower()
        assert "You are PI" in s
        assert "lead with the answer" in low
        assert "restat" in low  # the conditional case is still described
    assert "no acknowledgment" not in GC.lower()  # ban removed
