# Anthropic (Claude) LLM client for chat completions.
# Uses ANTHROPIC_API_KEY only; no OpenAI or other providers.

from __future__ import annotations

import os
import logging
from typing import Any, Dict, List, Optional

import anthropic

logger = logging.getLogger(__name__)

# Default model for chat (wealth planning, commentary, allocation)
DEFAULT_MODEL = "claude-3-5-sonnet-20241022"


def get_anthropic_client(api_key: Optional[str] = None) -> anthropic.Anthropic:
    """
    Create an Anthropic client instance.

    Args:
        api_key: Anthropic API key. If None, uses ANTHROPIC_API_KEY env var.

    Returns:
        Configured Anthropic client.

    Raises:
        ValueError: If no API key is provided or found.
    """
    key = api_key or os.getenv("ANTHROPIC_API_KEY")
    if not key or not key.strip():
        raise ValueError(
            "Anthropic API key required. "
            "Set ANTHROPIC_API_KEY environment variable or pass 'api_key' parameter."
        )
    logger.info("Initialized Anthropic client")
    return anthropic.Anthropic(api_key=key.strip())


def llm_chat(
    messages: List[Dict[str, Any]],
    model: str = DEFAULT_MODEL,
    max_tokens: int = 1024,
    temperature: float = 0.7,
    api_key: Optional[str] = None,
) -> str:
    """
    Send a list of messages (OpenAI-style: role + content) and return the assistant reply.
    System messages are merged into Anthropic's system parameter.

    Args:
        messages: List of {"role": "system"|"user"|"assistant", "content": "..."}.
        model: Claude model id.
        max_tokens: Max tokens to generate.
        temperature: Sampling temperature.
        api_key: Optional API key; otherwise uses ANTHROPIC_API_KEY.

    Returns:
        Assistant message text.
    """
    client = get_anthropic_client(api_key=api_key)
    system_parts: List[str] = []
    anthropic_messages: List[Dict[str, str]] = []

    for m in messages:
        role = (m.get("role") or "user").lower()
        content = m.get("content") or ""
        if isinstance(content, list):
            content = " ".join(
                b.get("text", b.get("content", "")) for b in content if isinstance(b, dict)
            )
        if role == "system":
            system_parts.append(content)
        else:
            anthropic_messages.append({"role": role, "content": content})

    system_text = "\n\n".join(system_parts) if system_parts else ""

    kwargs: Dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": anthropic_messages,
    }
    if system_text:
        kwargs["system"] = system_text
    if temperature is not None:
        kwargs["temperature"] = temperature

    response = client.messages.create(**kwargs)
    if not response.content:
        return ""
    first = response.content[0]
    if hasattr(first, "text"):
        return (first.text or "").strip()
    return str(first).strip()
