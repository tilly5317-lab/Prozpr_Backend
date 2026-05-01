"""Shared question-aware answer formatter.

Public API:
    format_answer(...)          — async LLM call producing customer-facing text
    format_with_telemetry(...)  — async wrapper that records a ChatAiModuleRun row
    assemble_prompt(...)        — pure function building the prompt dict (system + user)
    FORMATTER_HOUSE_STYLE       — shared brand-voice preamble
    FactsPack                   — type alias for the per-module facts dict
    ActionMode                  — Literal of action mode strings the formatter accepts
    FormatterFailure            — raised when the LLM call fails or returns unusable text
"""

from app.services.ai_bridge.answer_formatter.formatter import (
    ActionMode,
    FORMATTER_HOUSE_STYLE,
    FactsPack,
    FormatterFailure,
    assemble_prompt,
    format_answer,
    format_with_telemetry,
)

__all__ = [
    "ActionMode",
    "FORMATTER_HOUSE_STYLE",
    "FactsPack",
    "FormatterFailure",
    "assemble_prompt",
    "format_answer",
    "format_with_telemetry",
]
