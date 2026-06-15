"""The QA surface must emit through the forced tool and discard the reasoning field."""
from unittest.mock import MagicMock, patch

import market_commentary.chat_qa as qa


def test_answer_question_returns_answer_and_discards_reasoning():
    resp = MagicMock()
    resp.tool_calls = [{
        "name": "return_qa_answer",
        "args": {"reasoning": "SECRET WORKING OUT", "answer": "The repo rate is 5.25%."},
    }]
    with patch.object(qa, "_qa_llm_bound") as bound:
        bound.invoke.return_value = resp
        out = qa.answer_question("what's the repo rate?", document_content="... repo 5.25% ...")
    assert out == "The repo rate is 5.25%."
    assert "SECRET" not in out


def test_answer_question_falls_back_on_malformed_tool_call():
    resp = MagicMock()
    resp.tool_calls = []
    with patch.object(qa, "_qa_llm_bound") as bound:
        bound.invoke.return_value = resp
        out = qa.answer_question("?", document_content="doc")
    assert isinstance(out, str) and out.strip()   # safe fallback, no crash
