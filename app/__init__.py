"""Ask Tilly FastAPI application package root.

Aggregates ``routers`` (HTTP), ``services`` (business logic), ``models`` (SQLAlchemy ORM),
``schemas`` (Pydantic API shapes), and ``utils``.

Layout hints: ``services/chat_core`` runs one chat turn end-to-end; ``services/ai_bridge``
wraps LLM/agent code that lives under the sibling ``AI_Agents`` tree (added to ``sys.path``,
not edited by this app). ``models/profile`` and ``schemas/profile`` mirror CompleteProfile;
``routers/ai_modules`` plus ``schemas/ai_modules`` serve ``/api/v1/ai-modules``; ``schemas/ingest``
covers Finvu and MF AA ingestion payloads.
"""

