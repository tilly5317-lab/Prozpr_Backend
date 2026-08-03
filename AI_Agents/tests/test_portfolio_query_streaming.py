"""portfolio_query's LLM client: streaming, and the usage accounting it must keep.

This agent does not use the shared answer formatter — it owns its own forced-tool
call, so it needs its own streaming wiring and its own usage bookkeeping.
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

from langchain_core.messages import AIMessageChunk

from portfolio_query.llm_client import LLMClient
from token_stream import open_token_stream

TOOL = {
    "name": "portfolio_query_answer",
    "description": "answer",
    "input_schema": {
        "type": "object",
        "properties": {
            "guardrail_triggered": {"type": "boolean"},
            "answer": {"type": "string"},
        },
        "required": ["guardrail_triggered", "answer"],
    },
}

PIECES = ['{"guardrail_triggered": false, "answer": "', "Your ", "equity ", "sleeve.", '"}']


def _chunk(args: str, *, first: bool = False, **kw):
    # Only the opening chunk carries name/id, exactly as the wire format does;
    # repeating them on every chunk makes the merge produce no usable tool call.
    return AIMessageChunk(
        content="",
        tool_call_chunks=[
            {
                "name": TOOL["name"] if first else None,
                "args": args,
                "id": "call_1" if first else None,
                "index": 0,
                "type": "tool_call_chunk",
            }
        ],
        **kw,
    )


class _FakeLLM:
    """Duck-types the bind_tools -> astream/ainvoke surface of ChatAnthropic."""

    last_kwargs: dict = {}

    def __init__(self, **kwargs):
        _FakeLLM.last_kwargs = kwargs
        self.ainvoke_calls = 0

    def bind_tools(self, *_a, **_k):
        return self

    async def astream(self, _messages):
        for i, piece in enumerate(PIECES):
            # Usage lands only on the final chunk, and only on usage_metadata —
            # response_metadata["usage"] stays empty when streaming.
            if i == len(PIECES) - 1:
                yield _chunk(
                    piece,
                    usage_metadata={
                        "input_tokens": 700,
                        "output_tokens": 120,
                        "total_tokens": 820,
                        "input_token_details": {"cache_read": 11, "cache_creation": 22},
                    },
                )
            else:
                yield _chunk(piece, first=(i == 0))

    async def ainvoke(self, _messages):
        self.ainvoke_calls += 1
        return AIMessageChunk(
            content="",
            tool_calls=[
                {
                    "name": TOOL["name"],
                    "args": {"guardrail_triggered": False, "answer": "blocking"},
                    "id": "1",
                    "type": "tool_call",
                }
            ],
            response_metadata={
                "usage": {"input_tokens": 700, "output_tokens": 120,
                          "cache_creation_input_tokens": 22, "cache_read_input_tokens": 11}
            },
        )


def _call(*, stream_field, open_stream):
    async def run():
        client = LLMClient(api_key="test")
        with patch("portfolio_query.llm_client.ChatAnthropic", _FakeLLM):
            if not open_stream:
                return await client.call_structured(
                    "haiku", "sys", "user", tool=TOOL, stream_field=stream_field
                ), []
            async with open_token_stream() as stream:
                task = asyncio.create_task(
                    client.call_structured(
                        "haiku", "sys", "user", tool=TOOL, stream_field=stream_field
                    )
                )
                task.add_done_callback(lambda _: stream.close())
                deltas = [d async for d in stream]
                return await task, deltas

    return asyncio.run(run())


def test_streams_only_the_named_field():
    (data, _usage), deltas = _call(stream_field="answer", open_stream=True)
    assert "".join(deltas) == "Your equity sleeve."
    assert data["answer"] == "Your equity sleeve."
    # guardrail_triggered arrives first in the JSON but must never be streamed.
    assert "guardrail" not in "".join(deltas)


def test_usage_falls_back_to_usage_metadata_when_streaming():
    """response_metadata['usage'] is empty on the streaming path; without the
    fallback every streamed turn would record zero tokens and zero cost."""
    (_data, usage), _deltas = _call(stream_field="answer", open_stream=True)
    assert usage["input_tokens"] == 700
    assert usage["output_tokens"] == 120
    assert usage["cache_read_input_tokens"] == 11
    assert usage["cache_creation_input_tokens"] == 22


def test_blocking_path_untouched_without_a_stream():
    (data, usage), _ = _call(stream_field="answer", open_stream=False)
    assert data["answer"] == "blocking"
    assert usage["input_tokens"] == 700
    assert "betas" not in _FakeLLM.last_kwargs


def test_no_stream_field_means_no_streaming():
    """A caller that does not name a field must not stream anything."""
    (data, _usage), deltas = _call(stream_field=None, open_stream=True)
    assert deltas == []
    assert data["answer"] == "blocking"


def test_streaming_sets_the_fine_grained_beta():
    """Without it the API withholds tool JSON to the end and nothing streams."""
    _call(stream_field="answer", open_stream=True)
    assert "fine-grained-tool-streaming" in _FakeLLM.last_kwargs["betas"][0]
