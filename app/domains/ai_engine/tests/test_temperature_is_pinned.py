"""No LLM call may sample.

Omitting `temperature` applies the Anthropic default of 1.0. Measured on the
answer path: "when do I reach ₹10 crore?" returned FY2034/₹10.44cr and
FY2035/₹11.85cr on consecutive calls — different rupee figures, same customer,
same data. On the classifier, 4 of 101 labelled turns changed intent between
identical runs.
"""

from __future__ import annotations

import ast
from pathlib import Path

_REPO = Path(__file__).resolve().parents[4]
_ROOTS = (_REPO / "app", _REPO / "AI_Agents" / "src")

# Passes its kwargs as a dict, so the literal-call scan below cannot see them.
_KWARGS_CALLERS = {"answer_formatter/formatter.py"}


def _call_sites_without_temperature() -> list[str]:
    offenders: list[str] = []
    for root in _ROOTS:
        for path in root.rglob("*.py"):
            rel = str(path.relative_to(_REPO))
            if "/tests/" in rel or "/Testing/" in rel:
                continue
            if any(rel.endswith(k) for k in _KWARGS_CALLERS):
                continue
            for node in ast.walk(ast.parse(path.read_text(), filename=rel)):
                if isinstance(node, ast.Call) and _is_chat_anthropic(node.func):
                    if not any(kw.arg == "temperature" for kw in node.keywords):
                        offenders.append(f"{rel}:{node.lineno}")
    return offenders


def _is_chat_anthropic(func: ast.expr) -> bool:
    """A real call node — a mention in a comment or a bare attribute access
    (``ChatAnthropic.model_fields`` in the health router) never counts."""
    name = getattr(func, "id", None) or getattr(func, "attr", None)
    return name == "ChatAnthropic"


def test_every_chatanthropic_call_pins_temperature():
    offenders = _call_sites_without_temperature()

    assert not offenders, (
        "these LLM call sites would sample at the API default of 1.0: " + ", ".join(offenders)
    )


def test_the_formatter_defaults_to_zero():
    """It reads an env var; the default must be 0, not 'unset'."""
    src = (_REPO / "app/domains/ai_engine/answer_formatter/formatter.py").read_text()

    assert 'os.environ.get("AILAX_FORMATTER_TEMPERATURE", "0")' in src
