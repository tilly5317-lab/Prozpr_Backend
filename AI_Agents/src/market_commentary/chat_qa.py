from __future__ import annotations

import glob
import os
from typing import Optional

from langchain_anthropic import ChatAnthropic
from langchain_core.output_parsers import StrOutputParser

from .prompts import QA_PROMPT

_QA_MODEL = "claude-sonnet-4-6"
_QA_MAX_TOKENS = 1024

_qa_llm = ChatAnthropic(model=_QA_MODEL, max_tokens=_QA_MAX_TOKENS)

# Part 2 Q&A chain: {"document_content": str, "user_question": str} → str
qa_chain = QA_PROMPT | _qa_llm | StrOutputParser()


def load_latest_commentary(output_dir: str) -> str:
    """Find and read the most recent macro_snapshot_*.md file.

    Filenames follow the pattern macro_snapshot_YYYYMMDD_HHMMSS.md, so
    lexicographic sort equals chronological sort — the last element is newest.

    Raises:
        FileNotFoundError: If no commentary documents exist in output_dir.
    """
    pattern = os.path.join(output_dir, "macro_snapshot_*.md")
    files = sorted(glob.glob(pattern))
    if not files:
        raise FileNotFoundError(
            f"No market commentary documents found in {output_dir!r}. "
            "Run the daily pipeline first."
        )
    with open(files[-1]) as f:
        return f.read()


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
        output_dir: Directory containing macro_snapshot_*.md files.
        document_content: Pre-loaded document string (skips file I/O if provided,
            useful for caching across multiple questions in a session).

    Returns:
        A plain-text answer grounded in the latest market commentary.
    """
    if document_content is None:
        document_content = load_latest_commentary(output_dir)

    return qa_chain.invoke({
        "document_content": document_content,
        "user_question": user_question,
    })
