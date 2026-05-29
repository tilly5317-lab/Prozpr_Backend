"""Domain packages — domain-first phase of the proposed structure.

See ``docs/proposed_backend_structure.html`` for the full target layout. Each
domain folder owns its own vertical stack (``models/`` · ``schemas/`` ·
``routers/`` · ``services/``).

Currently populated:

- ``ai_engine`` — chat orchestrator, intent router, per-agent bridges
  (Phase 1 of the refactor; the rest of the domains still live at their
  pre-refactor locations under ``app/{models,schemas,routers,services}/``
  and will be migrated in later phases).
"""
