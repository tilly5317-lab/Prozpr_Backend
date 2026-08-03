from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage


class LLMClient:
    MODEL_MAP = {
        "haiku": "claude-haiku-4-5-20251001",
    }

    def __init__(self, api_key: str):
        self._api_key = api_key
        self.total_input_tokens = 0
        self.total_output_tokens = 0

    async def call_structured(
        self,
        model: str,
        system: str,
        user: str,
        *,
        tool: dict,
        max_tokens: int = 1024,
        stream_field: str | None = None,
    ) -> tuple[dict, dict]:
        """Call the model with a forced tool-use call; return the tool's input dict.

        ``tool`` must be a dict with keys ``name``, ``description``, ``input_schema``
        (Anthropic tool format). The model is forced via ``tool_choice`` to call
        exactly that tool, so the response always contains a single ``tool_use``
        block whose ``input`` is a dict matching ``input_schema`` — no JSON
        parsing or markdown-fence stripping needed on this side.

        ``stream_field`` names the customer-facing field to publish incrementally
        when the turn has an open token stream; callers must pass it explicitly so
        an internal field is never streamed to a customer by accident. With no
        open stream this is the exact ainvoke path it has always been.
        """
        from token_stream import (
            FINE_GRAINED_TOOL_STREAMING,
            astream_tool_answer,
            current_token_stream,
        )

        model_id = self.MODEL_MAP.get(model, model)
        streaming = stream_field is not None and current_token_stream() is not None
        # The beta is required or the API withholds the tool's input JSON until
        # the end and there is nothing to stream (see token_stream). temperature
        # stays a literal here — test_temperature_is_pinned scans the call text.
        llm = ChatAnthropic(
            model=model_id,
            max_tokens=max_tokens,
            api_key=self._api_key,
            temperature=0,
            **({"betas": [FINE_GRAINED_TOOL_STREAMING]} if streaming else {}),
        ).bind_tools(
            [tool],
            tool_choice={"type": "tool", "name": tool["name"]},
        )
        messages = [
            SystemMessage(
                content=[
                    {
                        "type": "text",
                        "text": system,
                        "cache_control": {"type": "ephemeral"},
                    }
                ]
            ),
            HumanMessage(content=user),
        ]
        if streaming:
            response = await astream_tool_answer(
                llm, messages, answer_field=stream_field
            )
        else:
            response = await llm.ainvoke(messages)

        tool_input: dict | None = None
        for tool_call in response.tool_calls:
            if tool_call["name"] == tool["name"]:
                tool_input = dict(tool_call["args"] or {})
                break
        if tool_input is None:
            raise RuntimeError(
                f"Forced tool-call returned no tool_use block named {tool['name']!r}"
            )
        usage_raw = (response.response_metadata or {}).get("usage")
        if not usage_raw:
            # The streaming path reports usage on `usage_metadata` and leaves
            # `response_metadata["usage"]` empty; without this fallback token and
            # cost accounting silently records zeros for every streamed turn.
            meta = getattr(response, "usage_metadata", None) or {}
            details = meta.get("input_token_details") or {}
            usage_raw = {
                "input_tokens": meta.get("input_tokens", 0),
                "output_tokens": meta.get("output_tokens", 0),
                "cache_creation_input_tokens": details.get("cache_creation", 0),
                "cache_read_input_tokens": details.get("cache_read", 0),
            }
        usage = self._record_usage(usage_raw)
        return tool_input, usage

    def _record_usage(self, usage_dict: dict) -> dict:
        usage = {
            "input_tokens": usage_dict.get("input_tokens", 0) or 0,
            "output_tokens": usage_dict.get("output_tokens", 0) or 0,
            "cache_creation_input_tokens": usage_dict.get(
                "cache_creation_input_tokens", 0
            )
            or 0,
            "cache_read_input_tokens": usage_dict.get("cache_read_input_tokens", 0)
            or 0,
        }
        self.total_input_tokens += usage["input_tokens"]
        self.total_output_tokens += usage["output_tokens"]
        return usage
