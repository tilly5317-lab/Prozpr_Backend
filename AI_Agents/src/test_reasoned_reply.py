"""Unit tests for the shared reasoning-first reply contract."""
from reasoned_reply import reasoned_reply_tool, extract_reasoned_reply


def test_thinking_field_declared_first_and_required():
    tool = reasoned_reply_tool(name="t", answer_description="the answer")
    props = list(tool["input_schema"]["properties"].keys())
    assert props[0] == "reasoning"          # first → conditions the answer tokens
    assert props[1] == "answer"
    assert tool["input_schema"]["required"] == ["reasoning", "answer"]


def test_custom_field_names_preserve_thinking_first():
    tool = reasoned_reply_tool(
        name="t", answer_field="document", answer_description="d",
        thinking_field="outline", thinking_description="plan here",
    )
    assert list(tool["input_schema"]["properties"].keys()) == ["outline", "document"]
    assert tool["input_schema"]["required"] == ["outline", "document"]


class _Resp:
    def __init__(self, tool_calls):
        self.tool_calls = tool_calls


def test_extract_returns_answer_and_discards_thinking():
    r = _Resp([{"name": "t", "args": {"reasoning": "SECRET", "answer": "Clean."}}])
    assert extract_reasoned_reply(r) == "Clean."


def test_extract_returns_none_on_missing_empty_or_malformed():
    assert extract_reasoned_reply(_Resp([])) is None
    assert extract_reasoned_reply(_Resp([{"name": "t", "args": {"answer": "  "}}])) is None
    assert extract_reasoned_reply(_Resp([{"name": "t", "args": {}}])) is None
    assert extract_reasoned_reply(_Resp(None)) is None


def test_extract_honours_custom_answer_field():
    r = _Resp([{"name": "t", "args": {"outline": "x", "document": "# Doc"}}])
    assert extract_reasoned_reply(r, answer_field="document") == "# Doc"
