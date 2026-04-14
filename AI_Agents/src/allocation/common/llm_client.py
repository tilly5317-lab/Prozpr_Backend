import anthropic
from dotenv import load_dotenv


class LLMClient:
    MODEL_MAP = {
        "haiku": "claude-haiku-4-5-20251001",
    }

    def __init__(self, api_key: str):
        self.client = anthropic.AsyncAnthropic(api_key=api_key)
        self.total_input_tokens = 0
        self.total_output_tokens = 0

    async def call(self, model: str, system: str, user: str, max_tokens: int = 1024) -> tuple[str, dict]:
        model_id = self.MODEL_MAP.get(model, model)
        response = await self.client.messages.create(
            model=model_id,
            max_tokens=max_tokens,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user}]
        )
        text = response.content[0].text
        usage = {
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
            "cache_creation_input_tokens": getattr(response.usage, "cache_creation_input_tokens", 0) or 0,
            "cache_read_input_tokens": getattr(response.usage, "cache_read_input_tokens", 0) or 0,
        }
        self.total_input_tokens += usage["input_tokens"]
        self.total_output_tokens += usage["output_tokens"]
        return text, usage
