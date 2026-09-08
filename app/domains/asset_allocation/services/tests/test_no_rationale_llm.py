"""The allocation pipeline is pure Python — no LLM, no env mutation.

``_invoke_pipeline`` used to pass ``rationale_fn=generate_rationales``, an LLM step
that wrote per-bucket prose, and to do it it assigned ``os.environ["ANTHROPIC_API_KEY"]``
around the call — process-global mutation under async concurrency, the same pattern
that was removed from the goal-planning summarizer.

The prose was also unread. ``service.py`` swaps the ideal output for the PRACTICAL
allocation before the facts pack is built, and the practical pipeline sets no
rationales (its only mention is a comment saying it deliberately avoids "that file's
LLM rationale plumbing"), so ``goals[].rationale`` was ``None`` on every successful
turn. It surfaced only on the fallback path where the practical engine throws.
"""

from __future__ import annotations

import inspect

from app.domains.asset_allocation.services.aa_engine import service


def test_the_rationale_llm_is_not_wired_in():
    source = inspect.getsource(service)

    assert "rationale_fn=" not in source
    assert "generate_rationales" not in source


def test_the_pipeline_call_does_not_mutate_the_environment():
    """Global env assignment races across concurrent turns.

    Checks executable lines only — the docstring explaining the removal
    legitimately names the pattern it forbids.
    """
    fn = service._invoke_pipeline
    code = inspect.getsource(fn).replace(fn.__doc__ or "", "")

    assert "os.environ" not in code
    assert "import os" not in inspect.getsource(service)


def test_invoke_pipeline_takes_no_api_key():
    """Nothing in the seven steps calls an LLM, so it needs no credential."""
    assert "anthropic_api_key" not in inspect.signature(service._invoke_pipeline).parameters
