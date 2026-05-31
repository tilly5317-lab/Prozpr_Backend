"""Ask Tilly FastAPI application package root.

Aggregates ``routers`` (HTTP), ``services`` (business logic), ``models`` (SQLAlchemy ORM),
``schemas`` (Pydantic API shapes), and ``utils``.

Layout hints: ``services/chat_core`` runs one chat turn end-to-end; ``services/ai_bridge``
wraps LLM/agent code that lives under the sibling ``AI_Agents`` tree (added to ``sys.path``,
not edited by this app). ``models/profile`` and ``schemas/profile`` mirror CompleteProfile;
``routers/ai_modules`` plus ``schemas/ai_modules`` serve ``/api/v1/ai-modules``; ``schemas/ingest``
covers Finvu and MF AA ingestion payloads.
"""

import sys as _sys
from pathlib import Path as _Path

# Inject AI_Agents/src into sys.path at package-init time so early app modules
# (e.g. app/models/profile/risk_profile.py — loaded by alembic env.py and at
# FastAPI boot before any ai_bridge import) can import shared helpers from
# AI_Agents/src/common directly. Mirrors the injection in
# app/services/ai_bridge/common.py:20 — this earlier hook covers the case
# where the ai_bridge layer isn't on the import path yet.
_AI_AGENTS_SRC = str(_Path(__file__).resolve().parent.parent / "AI_Agents" / "src")
if _AI_AGENTS_SRC not in _sys.path:
    _sys.path.insert(0, _AI_AGENTS_SRC)
