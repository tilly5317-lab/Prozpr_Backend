"""The allocation pipeline is pure Python — no LLM, no env mutation.

Step 7 once called Haiku for per-bucket prose (removed 2026-09-30). The prose was
never shown: chat displays the PRACTICAL allocation, which carries none. Step 7
now attaches fixed template sentences.
"""

from __future__ import annotations

import inspect
from pathlib import Path

from app.domains.asset_allocation.services.aa_engine import service


def test_the_allocation_engine_imports_no_llm():
    import asset_allocation_pydantic

    engine_dir = Path(asset_allocation_pydantic.__file__).parent
    for path in engine_dir.glob("**/*.py"):
        if {"Testing", "Master_testing"} & set(path.parts):
            continue
        source = path.read_text()
        assert "langchain_anthropic" not in source, path
        assert "ChatAnthropic" not in source, path


def test_the_pipeline_call_does_not_mutate_the_environment():
    """Global env assignment races across concurrent turns."""
    fn = service._invoke_pipeline
    code = inspect.getsource(fn).replace(fn.__doc__ or "", "")

    assert "os.environ" not in code
    assert "import os" not in inspect.getsource(service)


def test_invoke_pipeline_takes_no_api_key():
    """Nothing in the seven steps calls an LLM, so it needs no credential."""
    assert "anthropic_api_key" not in inspect.signature(service._invoke_pipeline).parameters
