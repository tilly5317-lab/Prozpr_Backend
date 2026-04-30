"""
One-off helper: add/expand module docstrings under app/ (run from repo root or backend/).

This script is idempotent-ish: it skips files whose existing docstring is already long enough.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
APP = BACKEND / "app"

# Keep existing module docs that are already substantial (avoids clobbering hand-written text).
MIN_LEN_TO_SKIP = 450


def _doc_for_path(rel: str) -> str:
    """Generate a detailed module docstring from ``app/...`` relative path."""
    rel = rel.replace("\\", "/")
    name = rel.split("/")[-1]

    if rel == "app/__init__.py":
        return (
            "Ask Tilly FastAPI application package root.\n\n"
            "Aggregates ``routers`` (HTTP API), ``services`` (business and integration logic), "
            "``models`` (SQLAlchemy ORM), ``schemas`` (Pydantic request/response shapes), "
            "and ``utils``. Chat and AI features use ``services.chat_core`` plus "
            "``services.ai_bridge`` (intent, market, allocation, liquidity) without modifying "
            "the separate ``AI_Agents`` tree."
        )

    if rel.startswith("app/services/ai_bridge/"):
        return (
            f"AI bridge — `{name}`.\n\n"
            "Sits between FastAPI services/routers and the ``AI_Agents/src`` packages (added to "
            "`sys.path` via ``ensure_ai_agents_path``). Handles env keys, async/thread boundaries, "
            "and user-context mapping. Goal-based allocation is invoked from here using "
            "``goal_based_allocation_pydantic`` (orchestrated by ``asset_allocation_service``) so "
            "`AI_Agents` files stay untouched."
        )

    if rel.startswith("app/services/chat_core/"):
        return (
            f"Chat core — `{name}`.\n\n"
            "Orchestrates a single user turn: intent classification, branch routing (market, "
            "portfolio query, portfolio-style spine with liquidity gate and allocation), optional "
            "telemetry, and assistant text. Depends on ``services.ai_bridge`` and preloaded ORM "
            "user context from ``get_ai_user_context``."
        )

    if rel.startswith("app/services/chat_brain/"):
        return (
            f"Chat brain (alternate package) — `{name}`.\n\n"
            "Parallel or legacy entry for chat orchestration; prefer ``services.chat_core`` for "
            "the HTTP chat router unless this package is explicitly wired."
        )

    if rel.startswith("app/services/effective_risk_profile/"):
        return (
            f"Effective risk profile — `{name}`.\n\n"
            "App-layer persistence and calculation helpers for the user’s effective risk assessment "
            "(distinct from the deterministic ``risk_profiling.scoring`` used when building "
            "``AllocationInput`` for ideal allocation)."
        )

    if rel.startswith("app/services/"):
        return (
            f"Application service — `{name}`.\n\n"
            "Encapsulates business logic consumed by FastAPI routers. Uses database sessions, "
            "optional external APIs, and other services; should remain free of route-specific "
            "HTTP details (status codes live in routers)."
        )

    if rel.startswith("app/routers/ai_modules/"):
        return (
            f"AI modules HTTP router — `{name}`.\n\n"
            "Exposes ``/api/v1/ai-modules/...`` style endpoints for debugging or direct module "
            "invocation. Not always on the live chat path; chat uses ``routers/chat`` + "
            "``ChatBrain`` instead."
        )

    if rel.startswith("app/routers/"):
        return (
            f"FastAPI router — `{name}`.\n\n"
            "Declares HTTP routes, dependencies (auth, DB session, user context), and maps "
            "request/response schemas. Delegates work to ``app.services`` and returns "
            "appropriate status codes and Pydantic models."
        )

    if rel.startswith("app/models/"):
        return (
            f"SQLAlchemy ORM model — `{name}`.\n\n"
            "Defines a database table mapping, columns, and relationships. Imported by services "
            "and Alembic migrations; avoid importing FastAPI or routers from here to prevent "
            "circular dependencies."
        )

    if rel.startswith("app/schemas/"):
        return (
            f"Pydantic schema — `{name}`.\n\n"
            "Request/response or DTO shapes for API validation and OpenAPI documentation. "
            "Kept separate from ORM models so API contracts can evolve independently of "
            "database columns."
        )

    if rel.startswith("app/utils/"):
        return (
            f"Shared utility — `{name}`.\n\n"
            "Small, reusable helpers (security, formatting) with no business workflow; "
            "safe to import from routers, services, or scripts."
        )

    if rel.startswith("app/"):
        return (
            f"Application module — `{name}`.\n\n"
            "Part of the Ask Tilly FastAPI backend under ``app/``. See sibling packages for "
            "routers, services, models, and schemas."
        )

    return f"Backend module — `{name}`."


def _replace_or_prepend_docstring(source: str, rel_posix: str) -> str | None:
    """Return new source with module docstring set; None if unchanged."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None

    new_doc = f'"""{_doc_for_path(rel_posix)}\n"""'

    if not tree.body:
        return new_doc + "\n\n" + source

    first = tree.body[0]
    old = ast.get_docstring(tree, clean=False)

    if old is not None and len(old.strip()) >= MIN_LEN_TO_SKIP:
        return None

    if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
        if isinstance(first.value.value, str):
            lineno0 = first.lineno - 1
            end0 = getattr(first, "end_lineno", first.lineno) - 1
            lines = source.splitlines(keepends=True)
            return "".join(lines[:lineno0] + [new_doc + "\n\n"] + lines[end0 + 1 :])

    insert_at = 0
    if isinstance(first, ast.ImportFrom) and first.module == "__future__":
        insert_at = 0
        lines = source.splitlines(keepends=True)
        return "".join(lines[:insert_at] + [new_doc + "\n\n"] + lines[insert_at:])

    lines = source.splitlines(keepends=True)
    return new_doc + "\n\n" + "".join(lines)


def main() -> int:
    if not APP.is_dir():
        print("app/ not found next to scripts/", file=sys.stderr)
        return 1

    changed = 0
    for path in sorted(APP.rglob("*.py")):
        rel = path.relative_to(BACKEND).as_posix()
        text = path.read_text(encoding="utf-8")
        new_text = _replace_or_prepend_docstring(text, rel)
        if new_text is None or new_text == text:
            continue
        path.write_text(new_text, encoding="utf-8", newline="\n")
        changed += 1
        print("updated", rel)

    print("done, files changed:", changed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
