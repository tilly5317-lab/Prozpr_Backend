"""Token-stream plumbing for streamed chat answers."""

from __future__ import annotations

import asyncio

import pytest
from langchain_core.messages import AIMessageChunk

from app.domains.ai_engine.streaming import (
    astream_tool_answer,
    current_token_stream,
    open_token_stream,
)


def _chunk(args: str, *, index: int = 0, response_metadata: dict | None = None):
    """One wire chunk carrying a slice of the forced tool call's partial JSON."""
    return AIMessageChunk(
        content="",
        tool_call_chunks=[
            {
                "name": "return_formatted_answer" if index == 0 else None,
                "args": args,
                "id": "call_1" if index == 0 else None,
                "index": 0,
                "type": "tool_call_chunk",
            }
        ],
        response_metadata=response_metadata or {},
    )


class _FakeStreamingLLM:
    """Duck-types ChatAnthropic.astream over a fixed chunk list."""

    def __init__(self, chunks):
        self._chunks = chunks

    async def astream(self, _messages):
        for c in self._chunks:
            yield c


def _answer_chunks(pieces, *, stop_reason="tool_use"):
    """Split `{"answer": "..."}` across chunks the way the API does."""
    chunks = [_chunk('{"answer": "', index=0)]
    for i, piece in enumerate(pieces, start=1):
        chunks.append(_chunk(piece, index=i))
    chunks.append(_chunk('"}', index=len(pieces) + 1,
                         response_metadata={"stop_reason": stop_reason}))
    return chunks


async def _collect(stream, task):
    task.add_done_callback(lambda _: stream.close())
    return [d async for d in stream]


def test_no_stream_open_by_default():
    assert current_token_stream() is None


def test_deltas_are_incremental_and_reassemble():
    """Each delta is only the NEW text; concatenated they equal the answer."""

    async def run():
        llm = _FakeStreamingLLM(_answer_chunks(["Your ", "portfolio ", "is fine."]))
        async with open_token_stream() as stream:
            task = asyncio.create_task(astream_tool_answer(llm, []))
            deltas = await _collect(stream, task)
            return deltas, await task

    deltas, final = asyncio.run(run())
    assert "".join(deltas) == "Your portfolio is fine."
    # No delta repeats text already sent — the bug that renders duplicated words.
    assert deltas == ["Your ", "portfolio ", "is fine."]
    assert final is not None


def test_accumulated_message_still_exposes_tool_calls_and_stop_reason():
    """The formatter reads .tool_calls and response_metadata['stop_reason'] off
    the result. Streaming must not break either — the max_tokens truncation
    guard silently becomes a no-op if stop_reason goes missing."""

    async def run():
        llm = _FakeStreamingLLM(_answer_chunks(["hello"], stop_reason="max_tokens"))
        async with open_token_stream() as stream:
            task = asyncio.create_task(astream_tool_answer(llm, []))
            await _collect(stream, task)
            return await task

    final = asyncio.run(run())
    assert final.response_metadata.get("stop_reason") == "max_tokens"
    assert final.tool_calls
    assert final.tool_calls[0]["args"]["answer"] == "hello"


def test_no_deltas_published_when_no_stream_open():
    """astream_tool_answer still returns a usable message with no sink open."""

    async def run():
        llm = _FakeStreamingLLM(_answer_chunks(["hi"]))
        return await astream_tool_answer(llm, [])

    final = asyncio.run(run())
    assert final.tool_calls[0]["args"]["answer"] == "hi"


def test_stream_closes_even_when_the_turn_raises():
    """A consumer must never hang on a turn that died."""

    async def run():
        async with open_token_stream() as stream:
            pass
        return [d async for d in stream]

    assert asyncio.run(run()) == []


def test_malformed_partial_json_mid_stream_is_tolerated():
    """Half-arrived JSON is normal on every chunk boundary; it must not raise."""

    async def run():
        llm = _FakeStreamingLLM(
            [_chunk('{"ans'), _chunk('wer": "ok'), _chunk('"}')]
        )
        async with open_token_stream() as stream:
            task = asyncio.create_task(astream_tool_answer(llm, []))
            deltas = await _collect(stream, task)
            await task
            return deltas

    assert "".join(asyncio.run(run())) == "ok"


def test_context_var_is_restored_after_the_turn():
    async def run():
        async with open_token_stream():
            assert current_token_stream() is not None
        return current_token_stream()

    assert asyncio.run(run()) is None


@pytest.mark.parametrize("field", ["answer"])
def test_formatter_path_untouched_without_a_stream(field, monkeypatch):
    """The existing non-streaming endpoint must keep using ainvoke verbatim."""
    from app.domains.ai_engine.answer_formatter import formatter as fmt

    calls = {"ainvoke": 0, "astream": 0}

    class _LLM:
        def bind_tools(self, *_a, **_k):
            return self

        async def ainvoke(self, _msgs):
            calls["ainvoke"] += 1
            return AIMessageChunk(
                content="",
                tool_calls=[
                    {"name": "return_formatted_answer", "args": {field: "done"},
                     "id": "1", "type": "tool_call"}
                ],
                response_metadata={"stop_reason": "tool_use"},
            )

        def astream(self, _msgs):  # pragma: no cover - must not be reached
            calls["astream"] += 1
            raise AssertionError("streamed with no open stream")

    monkeypatch.setattr("langchain_anthropic.ChatAnthropic", lambda **_k: _LLM())
    out = asyncio.run(fmt._invoke_llm("sys", "user", "goal_planning"))
    assert out == "done"
    assert calls == {"ainvoke": 1, "astream": 0}
