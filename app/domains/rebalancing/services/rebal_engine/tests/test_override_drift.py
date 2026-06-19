"""Drift guard: rebalancing override keys in _DETECT_REBAL_SYSTEM prompt
must match the ``_REBAL_ALLOWED_OVERRIDE_KEYS`` frozenset in ``overrides.py``.

Mirrors the AA chat drift guard. If someone adds an override key to the
frozenset without updating the prompt's "Allowed override keys" block (or
vice versa), the LLM will emit keys the code rejects (or fail to emit
keys the code accepts). This drift is silent at runtime but caught here
at CI.
"""

from __future__ import annotations

import re

from app.domains.rebalancing.services.rebal_engine.chat import _DETECT_REBAL_SYSTEM
from app.domains.rebalancing.services.rebal_engine.overrides import (
    _REBAL_ALLOWED_OVERRIDE_KEYS,
)


def test_rebal_override_keys_in_prompt_match_code() -> None:
    """Keys listed under counterfactual_explore's 'Allowed override keys'
    block must match the keys in ``_REBAL_ALLOWED_OVERRIDE_KEYS``.
    """
    # The block is bounded by the line "Allowed override keys" through
    # "Multiple keys are allowed".
    match = re.search(
        r"Allowed override keys.*?(?=\n  Multiple keys are allowed)",
        _DETECT_REBAL_SYSTEM,
        re.DOTALL,
    )
    assert match, (
        "Could not locate 'Allowed override keys' block in _DETECT_REBAL_SYSTEM. "
        "If the prompt was restyled, update this regex."
    )
    block = match.group(0)
    # Each key is on its own indented line: "    key_name: <range/type>"
    prompt_keys = set(
        re.findall(r"^\s+(\w+):\s+(?:number|true|false)", block, re.MULTILINE)
    )
    code_keys = set(_REBAL_ALLOWED_OVERRIDE_KEYS)
    assert prompt_keys == code_keys, (
        f"Override key drift between _DETECT_REBAL_SYSTEM prompt and "
        f"_REBAL_ALLOWED_OVERRIDE_KEYS frozenset in overrides.py:\n"
        f"  prompt: {sorted(prompt_keys)}\n"
        f"  code:   {sorted(code_keys)}\n"
        f"  prompt-only: {sorted(prompt_keys - code_keys)}\n"
        f"  code-only:   {sorted(code_keys - prompt_keys)}"
    )


def test_rebal_override_keys_in_field_description_match_code() -> None:
    """The ``RebalanceAction.overrides`` Field description is a SECOND copy of
    the allow-list the model sees (via the structured-output schema). It must
    also match ``_REBAL_ALLOWED_OVERRIDE_KEYS`` — this guards the copy that
    previously drifted (it had dropped ``additional_cash_inr``).
    """
    from app.domains.rebalancing.services.rebal_engine.chat import RebalanceAction

    desc = RebalanceAction.model_fields["overrides"].description or ""
    match = re.search(r"Allowed keys:\s*([^.]+)", desc)
    assert match, (
        "Could not locate the 'Allowed keys:' list in the overrides Field "
        f"description: {desc!r}"
    )
    desc_keys = {tok.strip() for tok in match.group(1).split(",") if tok.strip()}
    code_keys = set(_REBAL_ALLOWED_OVERRIDE_KEYS)
    assert desc_keys == code_keys, (
        f"Override key drift between the RebalanceAction.overrides Field "
        f"description and _REBAL_ALLOWED_OVERRIDE_KEYS frozenset in overrides.py:\n"
        f"  description: {sorted(desc_keys)}\n"
        f"  code:        {sorted(code_keys)}\n"
        f"  description-only: {sorted(desc_keys - code_keys)}\n"
        f"  code-only:        {sorted(code_keys - desc_keys)}"
    )
