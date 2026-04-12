"""Environment-backed settings (``.env`` loading, database URL, API keys).

``Settings`` centralizes secrets and feature flags: JWT auth, CORS, OpenAI, optional shared
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

_backend_dir = Path(__file__).resolve().parents[1]

for _env_path in (
    _backend_dir / ".env",
    Path.cwd() / ".env",
    Path.cwd() / "backend" / ".env",
    _backend_dir / ".env.example",
):
    if _env_path.exists():
        load_dotenv(_env_path)
        break
else:
    load_dotenv(_backend_dir / ".env")


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
    url = url.strip()
    if url.startswith("postgresql://") and not url.startswith("postgresql+asyncpg://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


def _database_url_from_db_env() -> str | None:
    """Build async Postgres URL from DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME."""
    host = (os.getenv("DB_HOST") or "").strip()
    user = (os.getenv("DB_USER") or "").strip()
    name = (os.getenv("DB_NAME") or "").strip()
    if not host or not user or not name:
        return None
    port = (os.getenv("DB_PORT") or "5432").strip() or "5432"
    password = os.getenv("DB_PASSWORD")
    password = password.strip() if password is not None else ""
    user_q = quote(user, safe="")
    pass_q = quote(password, safe="")
    auth = f"{user_q}:{pass_q}@" if password else f"{user_q}@"
    path = name.lstrip("/")
    return f"postgresql+asyncpg://{auth}{host}:{port}/{path}"


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


class Settings:
    PROJECT_NAME: str = "Ask Tilly API"
    API_V1_PREFIX: str = "/api/v1"
    VERSION: str = "2.0.0"

    ALLOWED_ORIGINS: list[str] = os.getenv(
        "ALLOWED_ORIGINS",
        "http://localhost:3000,http://localhost:5173,http://localhost:8080",
    ).split(",")

    @staticmethod
    def get_database_url() -> str:
        url = os.getenv("DATABASE_URL")
        if not url or not url.strip():
            load_dotenv(_backend_dir / ".env")
            url = os.getenv("DATABASE_URL")
        if not url or not url.strip():
            url = _database_url_from_db_env()
        if not url or not url.strip():
            raise RuntimeError(
                "DATABASE_URL is not set, and DB_HOST / DB_USER / DB_NAME are incomplete. "
                "Copy .env.example to .env and set either DATABASE_URL or all DB_* variables.\n"
                "Example: postgresql+asyncpg://user:password@localhost:5432/dbname"
            )
        url = _ensure_asyncpg_scheme(url)
        url = _normalize_database_url(url)
        url = _strip_pgbouncer_from_url(url)
        return url

    @staticmethod
    def get_jwt_secret() -> str:
        secret = os.getenv("JWT_SECRET", "").strip()
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
        key = (os.getenv("ENCRYPTION_KEY") or "").strip()
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
            v = (os.getenv(name) or "").strip()
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
        """Ideal mutual fund allocation (``Ideal_asset_allocation`` 5-step chain)."""
        return Settings._anthropic_key(
            "ASSET_ALLOCATION_API_KEY",
            "PORTFOLIO_QUERY_API_KEY",
            "ANTHROPIC_API_KEY",
        )

    @staticmethod
    def get_anthropic_risk_profiling_key() -> str | None:
        """Risk profiling LangChain module / related HTTP surfaces."""
        return Settings._anthropic_key("RISK_PROFILING_API_KEY", "ANTHROPIC_API_KEY")


@lru_cache
def get_settings() -> Settings:
    return Settings()
