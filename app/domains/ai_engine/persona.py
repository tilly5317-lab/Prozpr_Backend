"""Re-export of the shared persona builder for the app layer.

The canonical definition lives in ``AI_Agents/src/persona.py`` (stdlib-only,
importable by both layers). This module injects ``AI_Agents/src`` into sys.path
and re-exports, so app-layer consumers import from one place (mirrors
``app/domains/ai_engine/common.py``).
"""

from __future__ import annotations

import sys
from pathlib import Path

_AI_AGENTS_SRC = str((Path(__file__).resolve().parents[3] / "AI_Agents" / "src"))
if _AI_AGENTS_SRC not in sys.path:
    sys.path.insert(0, _AI_AGENTS_SRC)

from persona import (  # noqa: E402  re-exports
    PI_IDENTITY as PI_IDENTITY,
    FORMAT_PROFILES as FORMAT_PROFILES,
    build_system_prompt as build_system_prompt,
)
