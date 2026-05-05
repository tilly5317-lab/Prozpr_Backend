"""Drift guard: override keys in _DETECT_SYSTEM prompt must match the
``_ALLOWED_OVERRIDE_KEYS`` frozenset in ``overrides.py``.

If someone adds an override key to the frozenset without updating the prompt's
"ALLOWED override keys" block (or vice versa), the LLM will emit keys the
code rejects (or fail to emit keys the code accepts). This drift is silent
at runtime but caught here at CI.
"""

from __future__ import annotations

import re

from app.services.ai_bridge.asset_allocation.chat import _DETECT_SYSTEM
from app.services.ai_bridge.asset_allocation.overrides import _ALLOWED_OVERRIDE_KEYS


def test_override_keys_in_prompt_match_code() -> None:
    """Keys listed in ``_DETECT_SYSTEM``'s 'ALLOWED override keys' block
    must match the keys in ``_ALLOWED_OVERRIDE_KEYS``.
    """
    # Locate the "ALLOWED override keys" section, bounded by the blank-line
    # before "If the customer's value is out-of-range".
    match = re.search(
        r"ALLOWED override keys.*?(?=\n\nIf the customer's value)",
        _DETECT_SYSTEM,
        re.DOTALL,
    )
    assert match, (
        "Could not locate 'ALLOWED override keys' block in _DETECT_SYSTEM. "
        "If the prompt was restyled, update this regex."
    )
    block = match.group(0)
    # Each key is on its own indented line: "  key_name: <range/type>"
    prompt_keys = set(re.findall(r"^\s+(\w+):", block, re.MULTILINE))
    code_keys = set(_ALLOWED_OVERRIDE_KEYS)
    assert prompt_keys == code_keys, (
        f"Override key drift between _DETECT_SYSTEM prompt and "
        f"_ALLOWED_OVERRIDE_KEYS frozenset in overrides.py:\n"
        f"  prompt: {sorted(prompt_keys)}\n"
        f"  code:   {sorted(code_keys)}\n"
        f"  prompt-only: {sorted(prompt_keys - code_keys)}\n"
        f"  code-only:   {sorted(code_keys - prompt_keys)}"
    )
