"""Observability bootstrap — New Relic APM, plus the PostHog LLM client.

New Relic (below) must initialise before anything else is imported; PostHog is
independent of that constraint and is built at lifespan startup instead. Both
are no-ops unless their key is set, and neither may block boot.

New Relic APM bootstrap — initialise the agent before the app imports anything.

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

# Process-wide PostHog client (LLM observability). Built once at startup by
# ``init_posthog()``; None whenever capture is disabled or unavailable.
_posthog_client: object | None = None


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


def init_posthog() -> object | None:
    """Build the process-wide PostHog client used for LLM observability.

    Returns the client, or None when ``POSTHOG_API_KEY`` is unset or the
    ``posthog`` package is missing — in which case LLM capture is simply off and
    the app runs untouched (same contract as ``init_newrelic``).

    ``privacy_mode`` is set from ``POSTHOG_LLM_CAPTURE_CONTENT`` (default OFF).
    It is applied on the *client*, so it holds for every handler regardless of
    what any individual call site passes: the SDK redacts when either the client
    or the handler asks for it, so this is a floor that a call site cannot lower.
    """
    global _posthog_client
    if _posthog_client is not None:
        return _posthog_client

    from app.core.config import get_settings

    settings = get_settings()
    api_key = settings.get_posthog_api_key()
    if not api_key:
        logger.info("PostHog LLM observability disabled (POSTHOG_API_KEY not set).")
        return None

    try:
        from posthog import Posthog
    except ImportError:
        logger.warning(
            "POSTHOG_API_KEY is set but the 'posthog' package is not installed; "
            "skipping LLM observability. Run `pip install -r requirements.txt`."
        )
        return None

    capture_content = settings.posthog_llm_capture_content()
    try:
        _posthog_client = Posthog(
            api_key,
            host=settings.get_posthog_host(),
            privacy_mode=not capture_content,
        )
    except Exception as exc:  # pragma: no cover - defensive; never block boot
        logger.warning("PostHog init failed (continuing without LLM capture): %s", exc)
        return None

    logger.info(
        "PostHog LLM observability enabled (host=%s, prompt/state content=%s).",
        settings.get_posthog_host(),
        "CAPTURED" if capture_content else "redacted (privacy mode)",
    )
    return _posthog_client


def get_posthog_client() -> object | None:
    """The client built by ``init_posthog()``, or None when capture is off."""
    return _posthog_client


def shutdown_posthog() -> None:
    """Flush buffered events on app shutdown. Never raises."""
    global _posthog_client
    if _posthog_client is None:
        return
    try:
        _posthog_client.shutdown()
    except Exception as exc:  # pragma: no cover - shutdown must never raise
        logger.warning("PostHog shutdown failed: %s", exc)
    finally:
        _posthog_client = None


def capture_exception(
    exc: BaseException,
    *,
    distinct_id: str | None = None,
    properties: dict[str, object] | None = None,
) -> None:
    """Report an exception to PostHog error tracking, if the client is active.

    The PostHog analogue of ``notice_error`` (New Relic): the centralised handler
    in ``app/core/exceptions.py`` calls both, so a caught 5xx reaches both sinks.
    Reuses the process-wide client built by ``init_posthog`` for LLM observability.

    No-op when PostHog is disabled (``POSTHOG_API_KEY`` unset / package missing);
    best-effort — reporting must never raise into the request path. ``distinct_id``
    ties the error to a user when known (omitted for now — the global handler has
    no user context); ``properties`` carries extra context (e.g. the request path).
    """
    client = _posthog_client
    if client is None:
        return
    kwargs: dict[str, object] = {}
    if distinct_id is not None:
        kwargs["distinct_id"] = distinct_id
    if properties is not None:
        kwargs["properties"] = properties
    try:
        client.capture_exception(exc, **kwargs)
    except Exception:  # pragma: no cover - reporting must never raise
        pass


def capture_http_client_error(*, status_code: int, path: str, method: str) -> None:
    """Record a 4xx client-error response as a PostHog *event* (not an exception).

    A trend/volume signal, kept SEPARATE from 5xx error tracking: the count of
    client errors by status/path. Reuses the process-wide client. No-op when
    PostHog is disabled; best-effort — must never raise into the request path.

    Sent with ``$process_person_profile: False``: the middleware has no user
    context, so this must not create or mutate a person profile — it's an
    aggregate signal, not a per-user event.
    """
    client = _posthog_client
    if client is None:
        return
    try:
        client.capture(
            "http_client_error",
            distinct_id="backend",
            properties={
                "status_code": status_code,
                "path": path,
                "method": method,
                "$process_person_profile": False,
            },
        )
    except Exception:  # pragma: no cover - reporting must never raise
        pass


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
