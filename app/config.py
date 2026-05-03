"""Environment-backed settings (``.env`` loading, database URL, API keys).

``Settings`` centralizes secrets and feature flags: JWT auth, CORS (comma-separated origins,
``ALLOWED_ORIGINS=*`` / ``0.0.0.0/0`` / ``any`` for allow-any), OpenAI, optional shared
``ANTHROPIC_API_KEY``, and feature-specific Anthropic keys (intent, market commentary,
asset allocation, risk profiling, portfolio query) resolved with sensible fallbacks.
``get_settings`` is cached so repeated access does not re-parse the environment.
"""


from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlencode, urlparse, urlunparse

from dotenv import load_dotenv
from sqlalchemy.engine import make_url
from sqlalchemy.engine.url import URL

_backend_dir = Path(__file__).resolve().parents[1]

for _env_path in (
    _backend_dir / ".env",
    Path.cwd() / ".env",
    Path.cwd() / "backend" / ".env",
    _backend_dir / ".env.example",
):
    if _env_path.exists():
        load_dotenv(_env_path, encoding="utf-8-sig")
        break
else:
    load_dotenv(_backend_dir / ".env", encoding="utf-8-sig")


def _getenv(name: str, default: str | None = None) -> str | None:
    """Read env var and tolerate accidental UTF-8 BOM prefix in .env key names."""
    value = os.getenv(name)
    if value is not None:
        return value
    return os.getenv(f"\ufeff{name}", default)


def _strip_wrapping_quotes(raw: str) -> str:
    """Remove accidental outer quotes from .env values (e.g. DATABASE_URL=\"postgresql://...\")."""
    s = raw.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        return s[1:-1].strip()
    return s


def _normalize_database_url(url: str) -> str:
    url = url.strip()
    if "://" not in url or "@" not in url:
        return url
    scheme, rest = url.split("://", 1)
    if rest.count("@") < 1:
        return url
    userinfo, hostinfo = rest.rsplit("@", 1)
    if ":" in userinfo:
        user, _, password = userinfo.partition(":")
        password_decoded = unquote(password)
        password_encoded = quote(password_decoded, safe="")
        userinfo = f"{user}:{password_encoded}"
    return f"{scheme}://{userinfo}@{hostinfo}"


def _ensure_asyncpg_scheme(url: str) -> str:
    """Use asyncpg for Postgres URLs (Heroku/Railway often use postgres:// or postgresql://)."""
    url = url.strip()
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+asyncpg://", 1)
    if url.startswith("postgresql://") and not url.startswith("postgresql+asyncpg://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


def _ensure_async_sqlite_scheme(url: str) -> str:
    """Async engine requires aiosqlite driver, not default sqlite3."""
    url = url.strip()
    if url.startswith("sqlite://") and not url.startswith("sqlite+aiosqlite://"):
        return url.replace("sqlite://", "sqlite+aiosqlite://", 1)
    return url


def _database_url_from_postgres_env() -> str | None:
    """Build async Postgres URL from discrete env vars (e.g. RDS). Password can contain any characters.

    Uses POSTGRES_* (and DB_* aliases). Only used when ``DATABASE_URL`` is unset.
    Requires ``POSTGRES_HOST`` or ``DB_HOST``.
    """
    host = (_getenv("POSTGRES_HOST") or _getenv("DB_HOST") or "").strip()
    if not host:
        return None
    user = (_getenv("POSTGRES_USER") or _getenv("DB_USER") or "postgres").strip() or "postgres"
    password = _getenv("POSTGRES_PASSWORD", _getenv("DB_PASSWORD"))
    if password is None:
        password = ""
    database = (_getenv("POSTGRES_DB") or _getenv("DB_NAME") or "postgres").strip() or "postgres"
    port_s = (_getenv("POSTGRES_PORT") or _getenv("DB_PORT") or "5432").strip()
    try:
        port = int(port_s)
    except ValueError:
        port = 5432
    u = URL.create(
        drivername="postgresql+asyncpg",
        username=user,
        password=password,
        host=host,
        port=port,
        database=database,
    )
    return u.render_as_string(hide_password=False)


def _strip_pgbouncer_from_url(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.query:
        return url
    qs = parse_qs(parsed.query, keep_blank_values=True)
    qs.pop("pgbouncer", None)
    new_query = urlencode(qs, doseq=True)
    return urlunparse(
        (parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, parsed.fragment)
    )


def _normalize_asyncpg_ssl_query(url: str) -> str:
    """Normalize SSL query params for asyncpg URLs."""
    parsed = urlparse(url)
    if not parsed.query:
        return url
    qs = parse_qs(parsed.query, keep_blank_values=True)
    ssl_values = qs.get("ssl")
    if ssl_values:
        v = (ssl_values[-1] or "").strip().lower()
        if v in {"true", "1", "yes", "on", "require"}:
            qs["ssl"] = ["require"]
        elif v in {"false", "0", "no", "off", "disable"}:
            qs["ssl"] = ["disable"]
    new_query = urlencode(qs, doseq=True)
    return urlunparse(
        (parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, parsed.fragment)
    )


# Production site + local dev. Override with ALLOWED_ORIGINS in .env; use * or 0.0.0.0/0 to allow any Origin.
_DEFAULT_ALLOWED_ORIGINS = (
    "https://prozpr.in,http://prozpr.in,https://www.prozpr.in,http://www.prozpr.in,"
    "http://localhost:3000,http://localhost:5173,http://localhost:8080,http://13.127.210.211"
)


def _parse_cors_origins_env() -> tuple[list[str], bool]:
    """Parse ``ALLOWED_ORIGINS``: comma-separated URLs, or a single wildcard token.

    ``0.0.0.0/0`` is not a browser Origin (it is a firewall CIDR); we treat it like ``*``
    and use ``allow_origin_regex`` in FastAPI so ``allow_credentials=True`` still works.
    """
    raw = _strip_wrapping_quotes(_getenv("ALLOWED_ORIGINS", _DEFAULT_ALLOWED_ORIGINS) or "")
    token = raw.strip().lower()
    if token in ("*", "0.0.0.0/0", "any"):
        return [], True
    return [o.strip() for o in raw.split(",") if o.strip()], False


_CORS_ORIGINS, _CORS_ALLOW_ANY_ORIGIN = _parse_cors_origins_env()


class Settings:
    PROJECT_NAME: str = "Ask Tilly API"
    API_V1_PREFIX: str = "/api/v1"
    VERSION: str = "2.0.0"

    ALLOWED_ORIGINS: list[str] = _CORS_ORIGINS
    CORS_ALLOW_ANY_ORIGIN: bool = _CORS_ALLOW_ANY_ORIGIN

    @staticmethod
    def get_database_url() -> str:
        """Resolve DB URL: ``DATABASE_URL`` wins if set; otherwise build from ``POSTGRES_*`` / ``DB_*``."""
        url = _strip_wrapping_quotes(_getenv("DATABASE_URL") or "")
        # Common typo: DATABASE_URL=DATABASE_URL=postgresql://...
        if url.startswith("DATABASE_URL="):
            url = url.removeprefix("DATABASE_URL=").strip()
        if not url:
            load_dotenv(_backend_dir / ".env", encoding="utf-8-sig")
            url = _strip_wrapping_quotes(_getenv("DATABASE_URL") or "")
            if url.startswith("DATABASE_URL="):
                url = url.removeprefix("DATABASE_URL=").strip()
        if not url:
            url = (_database_url_from_postgres_env() or "").strip()
        if not url:
            raise RuntimeError(
                "Database URL is not configured. Either set DATABASE_URL in .env, or set "
                "POSTGRES_HOST, POSTGRES_USER, POSTGRES_PASSWORD, and POSTGRES_DB "
                "(see .env.example). For local Postgres: "
                "postgresql+asyncpg://user:password@localhost:5432/dbname"
            )
        url = _ensure_asyncpg_scheme(url)
        url = _ensure_async_sqlite_scheme(url)
        url = _normalize_database_url(url)
        url = _strip_pgbouncer_from_url(url)
        url = _normalize_asyncpg_ssl_query(url)
        try:
            parsed = make_url(url)
        except Exception as exc:
            raise RuntimeError(
                "DATABASE_URL could not be parsed by SQLAlchemy. Fix the string in .env — "
                "no spaces around '=', use postgresql+asyncpg://user:password@host:5432/dbname "
                "(URL-encode special characters in the password), or use discrete POSTGRES_* "
                f"variables instead. Underlying error: {exc}"
            ) from exc

        # Production and staging should use PostgreSQL (e.g. AWS RDS), not a local SQLite file.
        if parsed.drivername.startswith("sqlite"):
            allow_sqlite = (_getenv("ALLOW_SQLITE", "false") or "").strip().lower() in (
                "1",
                "true",
                "yes",
                "on",
            )
            if not allow_sqlite:
                raise RuntimeError(
                    "SQLite is disabled by default. The app is configured for PostgreSQL on AWS RDS "
                    "(or compatible). Set DATABASE_URL=postgresql+asyncpg://... or use POSTGRES_HOST, "
                    "POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB. "
                    "For local SQLite files only (e.g. wealth_agent.db), set ALLOW_SQLITE=true in .env."
                )
        return url

    @staticmethod
    def get_jwt_secret() -> str:
        secret = (_getenv("JWT_SECRET", "") or "").strip()
        if len(secret) >= 32:
            return secret
        if secret:
            raise RuntimeError("JWT_SECRET must be at least 32 characters")
        import logging
        logging.getLogger(__name__).warning(
            "JWT_SECRET not set: using dev default. Set JWT_SECRET in .env for production."
        )
        return "dev-secret-change-in-production-min-32-chars"

    @staticmethod
    def get_encryption_key() -> str:
        key = (_getenv("ENCRYPTION_KEY") or "").strip()
        if not key:
            raise RuntimeError(
                "ENCRYPTION_KEY is not set. Generate one with: "
                'python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
            )
        return key

    @staticmethod
    def _anthropic_key(*candidates: str) -> str | None:
        """First non-empty env value among candidate names."""
        for name in candidates:
            v = (_getenv(name) or "").strip()
            if v:
                return v
        return None

    @staticmethod
    def get_anthropic_key() -> str | None:
        """Shared Anthropic fallback when a feature-specific key is not set."""
        return Settings._anthropic_key("ANTHROPIC_API_KEY")

    @staticmethod
    def get_anthropic_intent_classifier_key() -> str | None:
        """Intent classifier (``AI_Agents/intent_classifier``)."""
        return Settings._anthropic_key("INTENT_CLASSIFIER_API_KEY", "ANTHROPIC_API_KEY")

    @staticmethod
    def get_anthropic_market_commentary_key() -> str | None:
        """Market commentary agent (macro scrape + document generation)."""
        return Settings._anthropic_key("MARKET_COMMENTARY_API_KEY", "ANTHROPIC_API_KEY")

    @staticmethod
    def get_anthropic_portfolio_query_key() -> str | None:
        """LLM-backed portfolio Q&A if wired; also legacy fallback for allocation keys."""
        return Settings._anthropic_key("PORTFOLIO_QUERY_API_KEY", "ANTHROPIC_API_KEY")

    @staticmethod
    def get_anthropic_asset_allocation_key() -> str | None:
        """Goal-based allocation (``goal_based_allocation_pydantic`` 7-step pipeline via ``asset_allocation_service``)."""
        return Settings._anthropic_key(
            "ASSET_ALLOCATION_API_KEY",
            "PORTFOLIO_QUERY_API_KEY",
            "ANTHROPIC_API_KEY",
        )

    @staticmethod
    def get_anthropic_risk_profiling_key() -> str | None:
        """Risk profiling LangChain module / related HTTP surfaces."""
        return Settings._anthropic_key("RISK_PROFILING_API_KEY", "ANTHROPIC_API_KEY")

    @staticmethod
    def mfapi_scheduler_enabled() -> bool:
        """Daily 00:00 IST mfapi.in MF master + NAV refresh. Default ON; set
        ``MFAPI_SCHEDULER_ENABLED=false`` (or 0/no/off) in tests/local dev."""
        raw = (_getenv("MFAPI_SCHEDULER_ENABLED") or "").strip().lower()
        if raw in {"0", "false", "no", "off"}:
            return False
        return True

    @staticmethod
    def get_openai_api_key() -> str | None:
        """OpenAI key for intent fallback, general chat, and market-commentary fallback (trimmed)."""
        v = (_getenv("OPENAI_API_KEY") or "").strip()
        if v:
            return v
        load_dotenv(_backend_dir / ".env", override=False, encoding="utf-8-sig")
        v = (_getenv("OPENAI_API_KEY") or "").strip()
        return v or None


@lru_cache
def get_settings() -> Settings:
    return Settings()
