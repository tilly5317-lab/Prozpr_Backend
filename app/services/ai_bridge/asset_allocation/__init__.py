"""Asset-allocation domain bridge — stubbed while replacement engine is pending.

NOTE: ``chat`` submodule is NOT auto-imported here (circular import via
``chat_core.turn_context``). Callers needing its ``@register`` side-effect
must import ``chat`` lazily.
"""

from app.services.ai_bridge.asset_allocation.input_builder import (
    build_goal_allocation_input_for_user,
)

# Back-compat alias for older imports/docs.
build_asset_allocation_input_for_user = build_goal_allocation_input_for_user
from app.services.ai_bridge.asset_allocation.persistence import (
    normalize_asset_allocation_engine_result,
    save_asset_allocation_from_engine_output,
)
from app.services.ai_bridge.asset_allocation.service import (
    AllocationRunOutcome,
    build_aa_facts_pack,
    build_fallback_brief,
    compose_allocation_chat_reply,
    compute_allocation_result,
    generate_asset_allocation_response,
)

__all__ = [
    "AllocationRunOutcome",
    "build_aa_facts_pack",
    "build_asset_allocation_input_for_user",
    "build_fallback_brief",
    "compose_allocation_chat_reply",
    "compute_allocation_result",
    "generate_asset_allocation_response",
    "normalize_asset_allocation_engine_result",
    "save_asset_allocation_from_engine_output",
]
