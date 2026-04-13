"""Thread-safe runner for the Ideal_asset_allocation 5-step LCEL chain.

The chain's module constructs ``ChatAnthropic`` clients at import time, so we
temporarily swap ``ANTHROPIC_API_KEY`` in the environment, reload the module
under a process-wide lock, invoke the chain, then restore the previous key.

Called from ``asset_allocation_service.compute_allocation_result`` inside
``asyncio.to_thread`` to keep the event loop responsive.
"""

from __future__ import annotations

import importlib
import os
import threading
from typing import Any

from app.services.ai_bridge.common import ensure_ai_agents_path

ensure_ai_agents_path()

from Ideal_asset_allocation.models import AllocationInput, AllocationOutput

# Only one thread may reload + invoke at a time (shared module state).
_LOCK = threading.Lock()


def _coerce_pct_to_int(obj: Any) -> Any:
    """Recursively round ``pct`` fields to int (Claude sometimes returns floats)."""
    if isinstance(obj, dict):
        return {
            k: int(round(float(v))) if k == "pct" and isinstance(v, (int, float)) and not isinstance(v, bool)
            else _coerce_pct_to_int(v)
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_coerce_pct_to_int(i) for i in obj]
    return obj


def invoke_ideal_allocation_with_full_state(
    alloc_input: AllocationInput,
    anthropic_api_key: str,
) -> tuple[dict, AllocationOutput]:
    """Run the chain and return ``(full_state, validated AllocationOutput)``."""
    ensure_ai_agents_path()

    key = anthropic_api_key.strip()
    with _LOCK:
        prev = os.environ.get("ANTHROPIC_API_KEY")
        os.environ["ANTHROPIC_API_KEY"] = key
        try:
            import Ideal_asset_allocation.main as ia_main
            importlib.reload(ia_main)
            full_state = ia_main.asset_allocation_chain.invoke(alloc_input.model_dump())
        finally:
            if prev is None:
                os.environ.pop("ANTHROPIC_API_KEY", None)
            else:
                os.environ["ANTHROPIC_API_KEY"] = prev

    # Normalise step5 output and coerce fractional pct fields.
    step5 = full_state.get("step5_presentation")
    if isinstance(step5, dict):
        if "client_summary" not in step5 and isinstance(step5.get("output"), dict):
            step5 = step5["output"]
        full_state = {**full_state, "step5_presentation": _coerce_pct_to_int(step5)}

    output = AllocationOutput.model_validate(full_state["step5_presentation"])
    return full_state, output
