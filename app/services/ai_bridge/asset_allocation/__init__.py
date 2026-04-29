"""Asset allocation domain — engine adapter, chat handler, and input builder.

Public surface re-exports the engine entry points and the input builder. The
``chat`` submodule is **not** auto-imported here: doing so triggers a circular
import via ``chat_core.turn_context``. Callers that need its ``@register``
side-effect must import ``chat`` lazily (e.g. inside a function body in
``chat_core/brain.py``).
"""

from app.services.ai_bridge.asset_allocation.service import (
    compute_allocation_result,
    format_allocation_chat_brief,
    generate_portfolio_optimisation_response,
)
from app.services.ai_bridge.asset_allocation.input_builder import (
    build_goal_allocation_input_for_user,
)

__all__ = [
    "build_goal_allocation_input_for_user",
    "compute_allocation_result",
    "format_allocation_chat_brief",
    "generate_portfolio_optimisation_response",
]
