"""Re-export of the chat token stream for the app layer.

The canonical definition lives in ``AI_Agents/src/token_stream.py`` — it has to,
because ``portfolio_query``'s LLM client is an agent under ``AI_Agents/src`` and
agents must not import the app layer. This module injects ``AI_Agents/src`` into
sys.path and re-exports, so app-layer consumers import from one place (mirrors
``app/domains/ai_engine/persona.py``).
"""

from __future__ import annotations

import sys
from pathlib import Path

_AI_AGENTS_SRC = str((Path(__file__).resolve().parents[3] / "AI_Agents" / "src"))
if _AI_AGENTS_SRC not in sys.path:
    sys.path.insert(0, _AI_AGENTS_SRC)

from token_stream import (  # noqa: E402  re-exports
    FINE_GRAINED_TOOL_STREAMING as FINE_GRAINED_TOOL_STREAMING,
    TokenStream as TokenStream,
    astream_tool_answer as astream_tool_answer,
    current_token_stream as current_token_stream,
    open_token_stream as open_token_stream,
)
