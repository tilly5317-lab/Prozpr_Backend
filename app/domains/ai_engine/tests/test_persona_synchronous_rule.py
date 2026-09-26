"""Guardrail: no deferred-work promises in the synchronous system prompt.

Background: rebalancing and other critical operations run synchronously within a
single turn. The persona must forbid promising to "come back", "work on it", or
use stall phrases that suggest the customer should wait for a background job.
"""

from __future__ import annotations


def test_persona_forbids_deferred_work_promises():
    """The system prompt must forbid promising deferred or background work."""
    import sys
    import pathlib

    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[4] / "AI_Agents" / "src"))
    from persona import build_system_prompt

    text = build_system_prompt("").lower()  # already includes SHARED_MECHANICS (persona.py:142)
    assert "one turn" in text  # from the new rule specifically, not an incidental match
    for banned in ("come back", "working on it", "give me a moment", "hang tight"):
        assert banned in text  # the rule names them verbatim as forbidden phrasings
