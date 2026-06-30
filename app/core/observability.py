"""New Relic APM bootstrap — initialise the agent before the app imports anything.

New Relic instruments third-party libraries (Starlette/FastAPI, asyncpg,
SQLAlchemy, httpx, the stdlib ``logging`` module …) by registering import hooks
when ``newrelic.agent.initialize()`` runs. For full coverage the agent must be
initialised **before** those libraries are first imported, so ``init_newrelic()``
is called as the very first statement in ``app/main.py`` (which every launch path
— ``uvicorn main:app``, ``uvicorn app.main:app``, ``python main.py`` — imports).

We initialise *programmatically* rather than via the ``newrelic-admin
run-program`` wrapper because this project keeps all secrets in a ``.env`` file
loaded by ``python-dotenv`` (see ``app/core/config.py``). The wrapper reads
``NEW_RELIC_*`` from the real process environment at launch, before that ``.env``
is loaded; initialising here — after we load ``.env`` ourselves — lets the agent
pick up ``NEW_RELIC_LICENSE_KEY`` and friends the same way every other secret is
provided.

Everything here is a **no-op unless ``NEW_RELIC_LICENSE_KEY`` is set**, so local
dev, CI, and the test suite run completely untouched. Any failure to initialise
is logged and swallowed — observability must never keep the API from booting.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# Prozpr_Backend/  (observability.py is app/core/observability.py → parents[2])
_BACKEND_DIR = Path(__file__).resolve().parents[2]

_initialized = False


def _apply_env_override(settings: object, attr: str, env_name: str) -> None:
    """Set ``settings.attr`` from ``env_name`` when the env var is non-empty."""
    value = (os.getenv(env_name) or "").strip()
    if value:
        setattr(settings, attr, value)


def init_newrelic() -> bool:
    """Initialise the New Relic agent. Returns True iff the agent was started.

    Safe to call more than once (subsequent calls are cheap no-ops). No-op when
    ``NEW_RELIC_LICENSE_KEY`` is unset or the ``newrelic`` package is missing.
    """
    global _initialized
    if _initialized:
        return True

    # Load .env so NEW_RELIC_* are visible before agent init — config.py loads
    # the same file later; load_dotenv is idempotent and won't override real env.
    for env_path in (_BACKEND_DIR / ".env", Path.cwd() / ".env"):
        if env_path.exists():
            load_dotenv(env_path, encoding="utf-8-sig")
            break

    if not (os.getenv("NEW_RELIC_LICENSE_KEY") or "").strip():
        logger.info("New Relic disabled (NEW_RELIC_LICENSE_KEY not set).")
        return False

    try:
        import newrelic.agent
    except ImportError:
        logger.warning(
            "NEW_RELIC_LICENSE_KEY is set but the 'newrelic' package is not "
            "installed; skipping APM. Run `pip install -r requirements.txt`."
        )
        return False

    config_file = (os.getenv("NEW_RELIC_CONFIG_FILE") or "").strip() or str(
        _BACKEND_DIR / "newrelic.ini"
    )
    # NEW_RELIC_ENVIRONMENT selects the [newrelic:<env>] section in the ini.
    environment = (os.getenv("NEW_RELIC_ENVIRONMENT") or "").strip() or None

    try:
        if Path(config_file).exists():
            newrelic.agent.initialize(config_file, environment)
        else:
            # No ini on disk — rely entirely on NEW_RELIC_* environment variables.
            logger.warning(
                "newrelic.ini not found at %s; initialising from environment only.",
                config_file,
            )
            newrelic.agent.initialize()

        # Agent 13.x does NOT override settings present in newrelic.ini with the
        # NEW_RELIC_* environment variables during programmatic initialize() — the
        # ini's (empty) license_key/app_name shadow the env. Since this project
        # keeps secrets in .env, push the connection-critical values on explicitly.
        # Env wins over the ini, matching New Relic's documented precedence.
        _apply_env_override(newrelic.agent.global_settings(), "license_key", "NEW_RELIC_LICENSE_KEY")
        _apply_env_override(newrelic.agent.global_settings(), "app_name", "NEW_RELIC_APP_NAME")
        _apply_env_override(newrelic.agent.global_settings(), "host", "NEW_RELIC_HOST")
    except Exception as exc:  # pragma: no cover - defensive; never block boot
        logger.warning("New Relic init failed (continuing without APM): %s", exc)
        return False

    _initialized = True
    logger.info(
        "New Relic agent initialised (app=%r, environment=%s).",
        os.getenv("NEW_RELIC_APP_NAME", "Prozpr Backend"),
        environment or "default",
    )
    return True


def notice_error() -> None:
    """Report the exception currently being handled to New Relic, if active.

    Used by the centralised exception handlers in ``app/core/exceptions.py``:
    those handlers catch exceptions and return JSON, so the agent's automatic
    error capture never sees them — we report them explicitly here. No-op when
    the agent isn't running or there's no active transaction.
    """
    try:
        import newrelic.agent

        newrelic.agent.notice_error()
    except Exception:  # pragma: no cover - reporting must never raise
        pass
