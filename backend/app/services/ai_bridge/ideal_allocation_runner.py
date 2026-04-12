"""Run ``Ideal_asset_allocation`` from the app layer without editing ``AI_Agents/``.

``Ideal_asset_allocation.main`` constructs ``ChatAnthropic`` clients at import time using
``ANTHROPIC_API_KEY``. The caller passes the key resolved from ``ASSET_ALLOCATION_API_KEY``
(via ``Settings.get_anthropic_asset_allocation_key``); this module temporarily assigns
``os.environ['ANTHROPIC_API_KEY']`` to that value,
uses ``importlib.reload`` on ``Ideal_asset_allocation.main`` so clients pick up the key from
``Settings``, invokes ``asset_allocation_chain``, then restores the previous env value.

A process-wide lock wraps reload + invoke because the interpreter shares one module object.
``asset_allocation_service`` calls this from ``asyncio.to_thread`` so the event loop stays
responsive during the five-step LCEL chain.
"""


from __future__ import annotations

import importlib
import os
import threading
from typing import Any

from app.services.ai_bridge.common import ensure_ai_agents_path

ensure_ai_agents_path()

from Ideal_asset_allocation.models import AllocationInput, AllocationOutput

_LOCK = threading.Lock()


def _coerce_pct_fields_to_int(obj: Any) -> Any:
    """
    Claude sometimes returns fractional percentages; ``AllocationOutput`` expects ``pct: int``.
    Recursively round any ``pct`` key (excluding bools) so Pydantic validation succeeds.
    """
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            if k == "pct" and isinstance(v, (int, float)) and not isinstance(v, bool):
                out[k] = int(round(float(v)))
            else:
                out[k] = _coerce_pct_fields_to_int(v)
        return out
    if isinstance(obj, list):
        return [_coerce_pct_fields_to_int(i) for i in obj]
    return obj


def invoke_ideal_allocation_with_full_state(
    alloc_input: AllocationInput,
    anthropic_api_key: str,
) -> tuple[dict, AllocationOutput]:
    """
    Returns (full invoke state dict including step1…step5, validated AllocationOutput).
    Thread-safe; uses a global lock because reload affects the shared module.
    """
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

    step5 = full_state.get("step5_presentation")
    if isinstance(step5, dict):
        if "client_summary" not in step5 and isinstance(step5.get("output"), dict):
            step5 = step5["output"]
        step5 = _coerce_pct_fields_to_int(step5)
        full_state = {**full_state, "step5_presentation": step5}

    output = AllocationOutput.model_validate(full_state["step5_presentation"])
    return full_state, output
