from __future__ import annotations

import os
from functools import cache
from typing import Optional

from langchain_anthropic import ChatAnthropic

from common import read_text_bom_aware
from reasoned_reply import reasoned_reply_tool, extract_reasoned_reply

from .prompts import QA_PROMPT

_DOCUMENT_FILENAME = "market_commentary_latest.md"

_QA_MODEL = "claude-sonnet-4-6"
_QA_MAX_TOKENS = 1500  # was 1024 — room for the discarded reasoning field

# Force the model to think privately, then return a clean answer. The reasoning field is
# declared first (so it conditions the answer) and discarded by extract_reasoned_reply.
_QA_TOOL = reasoned_reply_tool(
    name="return_qa_answer",
    answer_description=(
        "The clean, customer-facing answer grounded ONLY in the market-commentary "
        "document. 2-5 short sentences. No preamble, no working-out."
    ),
)


@cache
def _get_qa_llm():
    """Build (and cache) the QA LLM on first call (lazy so ``ChatAnthropic``
    reads ``ANTHROPIC_API_KEY`` at call time, not at import)."""
    llm = ChatAnthropic(model=_QA_MODEL, max_tokens=_QA_MAX_TOKENS)
    return llm.bind_tools(
        [_QA_TOOL], tool_choice={"type": "tool", "name": "return_qa_answer"}
    )


def load_latest_commentary(output_dir: str) -> str:
    """Read the most recent market commentary document.

    Raises:
        FileNotFoundError: If the commentary document does not exist in output_dir.
    """
    path = os.path.join(output_dir, _DOCUMENT_FILENAME)
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"No market commentary document found at {path!r}. "
            "Run the daily pipeline first."
        )
    # BOM-aware: the .md may be UTF-16 (PowerShell) or UTF-8; never trust the OS locale.
    return read_text_bom_aware(path)


def answer_question(
    user_question: str,
    output_dir: str = ".",
    document_content: Optional[str] = None,
) -> str:
    """Answer a user question grounded in the latest market commentary document.

    Reads the stored commentary document (Part 1 output) and answers using
    only that content — no re-scraping or re-extraction.

    Args:
        user_question: The user's natural-language question.
        output_dir: Directory containing ``market_commentary_latest.md``.
        document_content: Pre-loaded document string (skips file I/O if provided,
            useful for caching across multiple questions in a session).

    Returns:
        A plain-text answer grounded in the latest market commentary.
    """
    if document_content is None:
        document_content = load_latest_commentary(output_dir)

    messages = QA_PROMPT.format_messages(
        document_content=document_content,
        user_question=user_question,
    )
    answer = extract_reasoned_reply(_get_qa_llm().invoke(messages))
    if answer:
        return answer
    # Rare malformed tool call — return a safe, on-voice fallback rather than crash.
    return "I couldn't find that in the latest market commentary — could you rephrase?"
