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
    ) -> tuple[dict, dict]:
        """Call the model with a forced tool-use call; return the tool's input dict.

        ``tool`` must be a dict with keys ``name``, ``description``, ``input_schema``
        (Anthropic tool format). The model is forced via ``tool_choice`` to call
        exactly that tool, so the response always contains a single ``tool_use``
        block whose ``input`` is a dict matching ``input_schema`` — no JSON
        parsing or markdown-fence stripping needed on this side.
        """
        model_id = self.MODEL_MAP.get(model, model)
        llm = ChatAnthropic(
            model=model_id,
            max_tokens=max_tokens,
            api_key=self._api_key,
        ).bind_tools(
            [tool],
            tool_choice={"type": "tool", "name": tool["name"]},
        )
        response = await llm.ainvoke([
            SystemMessage(content=[
                {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
            ]),
            HumanMessage(content=user),
        ])

        tool_input: dict | None = None
        for tool_call in response.tool_calls:
            if tool_call["name"] == tool["name"]:
                tool_input = dict(tool_call["args"] or {})
                break
        if tool_input is None:
            raise RuntimeError(
                f"Forced tool-call returned no tool_use block named {tool['name']!r}"
            )
        usage = self._record_usage(response.response_metadata.get("usage") or {})
        return tool_input, usage

    def _record_usage(self, usage_dict: dict) -> dict:
        usage = {
            "input_tokens": usage_dict.get("input_tokens", 0) or 0,
            "output_tokens": usage_dict.get("output_tokens", 0) or 0,
            "cache_creation_input_tokens": usage_dict.get("cache_creation_input_tokens", 0) or 0,
            "cache_read_input_tokens": usage_dict.get("cache_read_input_tokens", 0) or 0,
        }
        self.total_input_tokens += usage["input_tokens"]
        self.total_output_tokens += usage["output_tokens"]
        return usage
