"""Drift guard: override keys in _DETECT_SYSTEM prompt must match the
``_ALLOWED_OVERRIDE_KEYS`` frozenset in ``overrides.py``.

If someone adds an override key to the frozenset without updating the prompt's
"ALLOWED override keys" block (or vice versa), the LLM will emit keys the
code rejects (or fail to emit keys the code accepts). This drift is silent
at runtime but caught here at CI.
"""

from __future__ import annotations

import re

from app.domains.asset_allocation.services.aa_engine.chat import _DETECT_SYSTEM
from app.domains.asset_allocation.services.aa_engine.overrides import (
    _ALLOWED_OVERRIDE_KEYS,
)

# Keys in _ALLOWED_OVERRIDE_KEYS that belong to a DIFFERENT one-off-override
# consumer than the AA what-if chat detector below, and so are never meant to
# appear in its "ALLOWED override keys" prompt block. human_override_preferences
# is a structured dict consumed by the PAA input builder's preference merge
# (build_practical_allocation_input_for_user); the AA what-if LLM only ever
# emits scalar overrides.
_NON_AA_CHAT_KEYS = frozenset({"human_override_preferences"})


def test_override_keys_in_prompt_match_code() -> None:
    """Keys listed in ``_DETECT_SYSTEM``'s 'ALLOWED override keys' block
    must match the keys in ``_ALLOWED_OVERRIDE_KEYS`` (minus keys owned by
    other one-off-override consumers, see ``_NON_AA_CHAT_KEYS``).
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
    code_keys = set(_ALLOWED_OVERRIDE_KEYS) - _NON_AA_CHAT_KEYS
    assert prompt_keys == code_keys, (
        f"Override key drift between _DETECT_SYSTEM prompt and "
        f"_ALLOWED_OVERRIDE_KEYS frozenset in overrides.py:\n"
        f"  prompt: {sorted(prompt_keys)}\n"
        f"  code:   {sorted(code_keys)}\n"
        f"  prompt-only: {sorted(prompt_keys - code_keys)}\n"
        f"  code-only:   {sorted(code_keys - prompt_keys)}"
    )
