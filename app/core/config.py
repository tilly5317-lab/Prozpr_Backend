"""Environment-backed settings (``.env`` loading, database URL, API keys).

``Settings`` centralizes secrets and feature flags: JWT auth, CORS (comma-separated origins,
``ALLOWED_ORIGINS=*`` / ``0.0.0.0/0`` / ``any`` for allow-any), optional shared
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

# config.py is app/core/config.py -> parents[2] is the Prozpr_Backend root, which is
# where .env actually lives. parents[1] (the app/ package) has no .env, so the loader
# silently fell through to Path.cwd() and only worked because PM2 sets cwd to the root.
_backend_dir = Path(__file__).resolve().parents[2]

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
    user = (
        _getenv("POSTGRES_USER") or _getenv("DB_USER") or "postgres"
    ).strip() or "postgres"
    password = _getenv("POSTGRES_PASSWORD", _getenv("DB_PASSWORD"))
    if password is None:
        password = ""
    database = (
        _getenv("POSTGRES_DB") or _getenv("DB_NAME") or "postgres"
    ).strip() or "postgres"
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
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            new_query,
            parsed.fragment,
        )
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
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            new_query,
            parsed.fragment,
        )
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
    raw = _strip_wrapping_quotes(
        _getenv("ALLOWED_ORIGINS", _DEFAULT_ALLOWED_ORIGINS) or ""
    )
    token = raw.strip().lower()
    if token in ("*", "0.0.0.0/0", "any"):
        return [], True
    return [o.strip() for o in raw.split(",") if o.strip()], False


_CORS_ORIGINS, _CORS_ALLOW_ANY_ORIGIN = _parse_cors_origins_env()


class Settings:
    PROJECT_NAME: str = "Ask PI API"
    API_V1_PREFIX: str = "/api/v1"
    VERSION: str = "2.0.0"

    ALLOWED_ORIGINS: list[str] = _CORS_ORIGINS
    CORS_ALLOW_ANY_ORIGIN: bool = _CORS_ALLOW_ANY_ORIGIN

    # Which deployment this process is. Feeds PostHog super_properties and the OTel
    # resource, so prod and staging events are distinguishable. New Relic used to
    # provide this separation via app_name; nothing else does once it is gone.
    DEPLOY_ENV: str = (
        _getenv("DEPLOY_ENV", "development") or "development"
    ).strip() or "development"

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
        """Anthropic key reserved for the goal-based allocation chat path (bridge is stubbed until the new engine ships)."""
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
    def get_anthropic_rebalancing_key() -> str | None:
        """Rebalancing chat classifier (mutual-fund rebalancing flow)."""
        return Settings._anthropic_key("REBALANCING_API_KEY", "ANTHROPIC_API_KEY")

    @staticmethod
    def get_anthropic_additional_investment_key() -> str | None:
        """Additional-investment chat extractor (deploy amount + cadence)."""
        return Settings._anthropic_key(
            "ADDITIONAL_INVESTMENT_API_KEY", "ANTHROPIC_API_KEY"
        )

    @staticmethod
    def get_anthropic_answer_formatter_key() -> str | None:
        """Shared answer-formatter LLM call (used by AA + rebalancing chat formatters)."""
        return Settings._anthropic_key("ANSWER_FORMATTER_API_KEY", "ANTHROPIC_API_KEY")

    @staticmethod
    def get_anthropic_general_chat_key() -> str | None:
        """General-chat (out-of-scope / casual) reply generator."""
        return Settings._anthropic_key("GENERAL_CHAT_API_KEY", "ANTHROPIC_API_KEY")

    @staticmethod
    def get_anthropic_formatter_key_for(module_name: str) -> str | None:
        """Answer-formatter key, attributed to the module whose reply it writes.

        Every formatter call used to bill to one key, so a module's reply cost was
        invisible — and goal_planning, whose only module-owned LLM call was removed,
        had no attributable spend at all. Resolution order: the module's own key,
        then the shared formatter key, then the global fallback.

        A no-op until distinct keys are actually set: today every per-module env
        var in .env holds the same value as ANTHROPIC_API_KEY.
        """
        module_var = f"{module_name.upper()}_API_KEY"
        return Settings._anthropic_key(
            module_var,
            f"ANTHROPIC_{module_var}",
            "ANSWER_FORMATTER_API_KEY",
            "ANTHROPIC_API_KEY",
        )

    @staticmethod
    def get_anthropic_goal_planning_key() -> str | None:
        """Goal planning LangGraph agent + Haiku-based NL extractor."""
        return Settings._anthropic_key(
            "ANTHROPIC_GOAL_PLANNING_API_KEY", "ANTHROPIC_API_KEY"
        )

    @staticmethod
    def mfapi_scheduler_enabled() -> bool:
        """Daily 00:00 IST mfapi.in MF master + NAV refresh. Default ON; set
        ``MFAPI_SCHEDULER_ENABLED=false`` (or 0/no/off) in tests/local dev."""
        raw = (_getenv("MFAPI_SCHEDULER_ENABLED") or "").strip().lower()
        if raw in {"0", "false", "no", "off"}:
            return False
        return True

    @staticmethod
    def benchmark_scheduler_enabled() -> bool:
        """Thrice-daily (09:30/14:30/21:30 IST) benchmark EOD refresh. Default ON;
        set ``BENCHMARK_SCHEDULER_ENABLED=false`` (or 0/no/off) in tests/local dev.

        The legacy ``INDEX_TRI_SCHEDULER_ENABLED`` flag is still honoured for
        back-compat (either flag set to a falsey value disables the job)."""
        for var in ("BENCHMARK_SCHEDULER_ENABLED", "INDEX_TRI_SCHEDULER_ENABLED"):
            raw = (_getenv(var) or "").strip().lower()
            if raw in {"0", "false", "no", "off"}:
                return False
        return True

    @staticmethod
    def skip_startup_db_ddl() -> bool:
        """Skip ``create_all_tables`` and Postgres schema patches on startup (faster against RDS).

        Default OFF. When true, run ``alembic upgrade head`` (or ensure tables exist) separately.
        """
        raw = (_getenv("SKIP_STARTUP_DB_DDL") or "").strip().lower()
        return raw in {"1", "true", "yes", "on"}

    # ── Issue reports (support domain): Zoho SMTP + Excel register ─────────
    @staticmethod
    def get_smtp_host() -> str:
        return (_getenv("SMTP_HOST") or "smtp.zoho.com").strip()

    @staticmethod
    def get_smtp_port() -> int:
        raw = (_getenv("SMTP_PORT") or "465").strip()
        try:
            return int(raw)
        except ValueError:
            return 465

    @staticmethod
    def get_smtp_user() -> str:
        """Authenticated Zoho mailbox; Zoho requires From == this address."""
        return (_getenv("SMTP_USER") or "support@prozpr.com").strip()

    @staticmethod
    def get_smtp_password() -> str | None:
        """Zoho app-specific password. When unset, issue emails are skipped
        (the report still lands in the Excel log)."""
        v = (_getenv("SMTP_PASSWORD") or "").strip()
        return v or None

    @staticmethod
    def get_support_email_to() -> str:
        """Inbox that receives issue-report notifications."""
        return (_getenv("SUPPORT_EMAIL_TO") or "support@prozpr.com").strip()

    @staticmethod
    def get_issue_sheet_webhook_url() -> str | None:
        """Google Apps Script web-app URL that appends a row to the shared
        Google Sheet issue register — the sole register, so this must be set
        for issue reporting to work (an unset value makes report-issue 503)."""
        v = (_getenv("ISSUE_SHEET_WEBHOOK_URL") or "").strip()
        return v or None

    @staticmethod
    def get_issue_sheet_token() -> str | None:
        """Shared secret the Apps Script checks so strangers cannot post junk
        rows if the webhook URL ever leaks."""
        v = (_getenv("ISSUE_SHEET_TOKEN") or "").strip()
        return v or None

    # ── New-signup team notification (identity domain): Slack + optional Sheet ──
    @staticmethod
    def get_slack_signup_webhook_url() -> str | None:
        """Slack Incoming Webhook URL that posts each new signup to a team
        channel (e.g. #signups) — the primary "everyone gets notified" channel.
        When unset, the Slack ping is skipped (signup still succeeds)."""
        v = (_getenv("SLACK_SIGNUP_WEBHOOK_URL") or "").strip()
        return v or None

    @staticmethod
    def get_signup_sheet_webhook_url() -> str | None:
        """Optional Google Apps Script web-app URL that appends one row per
        signup to a shared 'New Signups' Google Sheet (the sortable
        'all users in one place' view). When unset, the sheet append is
        skipped — Slack remains the primary channel."""
        v = (_getenv("SIGNUP_SHEET_WEBHOOK_URL") or "").strip()
        return v or None

    @staticmethod
    def get_signup_sheet_token() -> str | None:
        """Shared secret the signup-sheet Apps Script checks so strangers
        cannot post junk rows if the webhook URL ever leaks."""
        v = (_getenv("SIGNUP_SHEET_TOKEN") or "").strip()
        return v or None

    # ── Fintech Primitives (execution domain): sandbox order execution ──────
    @staticmethod
    def get_fp_base_url() -> str:
        return (_getenv("FP_BASE_URL") or "https://s.finprim.com").strip().rstrip("/")

    @staticmethod
    def get_fp_tenant() -> str | None:
        v = (_getenv("FP_TENANT") or "").strip()
        return v or None

    @staticmethod
    def get_fp_api_key() -> str | None:
        v = (_getenv("FP_API_KEY") or "").strip()
        return v or None

    @staticmethod
    def get_fp_api_secret() -> str | None:
        v = (_getenv("FP_API_SECRET") or "").strip()
        return v or None

    @staticmethod
    def fp_enabled() -> bool:
        """FP order execution is inert unless the tenant credentials are set."""
        return bool(
            Settings.get_fp_tenant()
            and Settings.get_fp_api_key()
            and Settings.get_fp_api_secret()
        )

    @staticmethod
    def get_fp_sandbox_schemes() -> list[str]:
        """ISINs known to be transactable on the FP sandbox tenant. The sandbox
        only enables a handful of ICICI schemes — any other ISIN 400s with
        "scheme is not available for transaction". Override via
        ``FP_SANDBOX_SCHEMES`` (comma-separated ISINs) as FP enables more."""
        raw = (_getenv("FP_SANDBOX_SCHEMES") or "").strip()
        if raw:
            return [s.strip().upper() for s in raw.split(",") if s.strip()]
        return [
            "INF109K01423",
            "INF109KC1TY0",
            "INF109KC1TV6",
            "INF109KC1TU8",
            "INF109K01605",
            "INF109KC11U2",
            "INF109KC19T7",
        ]

    @staticmethod
    def get_fp_scheme_gateway() -> str:
        """Gateway segment for FP scheme-plan lookups
        (``/v2/mf_scheme_plans/{gateway}/{isin}``). Override via
        ``FP_SCHEME_GATEWAY``."""
        return (_getenv("FP_SCHEME_GATEWAY") or "cybrillapoa").strip()

    @staticmethod
    def fp_test_reports_enabled() -> bool:
        """Whether the SIMULATED FP folios/holdings/returns reports are served.
        The sandbox can't produce real folios (nothing settles), so these are
        demo-only test data. Default: on for the sandbox host, off elsewhere;
        force with ``FP_TEST_REPORTS_ENABLED=true|false``. Must stay OFF in
        production — it fabricates holdings."""
        raw = (_getenv("FP_TEST_REPORTS_ENABLED") or "").strip().lower()
        if raw in ("1", "true", "yes", "on"):
            return True
        if raw in ("0", "false", "no", "off"):
            return False
        return "s.finprim.com" in Settings.get_fp_base_url()

    @staticmethod
    def get_fp_preverify_tenant() -> str:
        return (_getenv("FP_PREVERIFY_TENANT") or "cybrillarta").strip()

    @staticmethod
    def get_fp_preverify_client_id() -> str | None:
        v = (_getenv("FP_PREVERIFY_CLIENT_ID") or "").strip()
        return v or None

    @staticmethod
    def get_fp_preverify_client_secret() -> str | None:
        v = (_getenv("FP_PREVERIFY_CLIENT_SECRET") or "").strip()
        return v or None

    @staticmethod
    def fp_preverify_enabled() -> bool:
        """The Pre-Verification (KYC) service has its own tenant + creds."""
        return bool(
            Settings.get_fp_preverify_client_id()
            and Settings.get_fp_preverify_client_secret()
        )

    # ── CAS Parser API (casparser.in): remote CAS PDF parsing + CAS-to-email ──
    @staticmethod
    def get_casparser_base_url() -> str:
        return (
            (_getenv("CASPARSER_BASE_URL") or "https://api.casparser.in")
            .strip()
            .rstrip("/")
        )

    @staticmethod
    def get_casparser_api_key() -> str | None:
        """API key for api.casparser.in (`x-api-key` header). The docs' sandbox
        key ``sandbox-with-json-responses`` works for wiring tests without
        consuming credits."""
        v = (_getenv("CASPARSER_API_KEY") or "").strip()
        return v or None

    @staticmethod
    def casparser_enabled() -> bool:
        """CAS statement import is inert (503s) unless the API key is set."""
        return bool(Settings.get_casparser_api_key())

    @staticmethod
    def get_casparser_multipart_max_bytes() -> int:
        """Largest CAS PDF sent to casparser as a multipart upload. Their edge
        caps request bodies plan-dependently — support (2026-08-04): "2MB
        (about 1.8 in reality)" — so bigger files go via ``pdf_url`` instead
        (see ``get_public_api_base_url``)."""
        raw = (_getenv("CASPARSER_MULTIPART_MAX_BYTES") or "").strip()
        try:
            return int(raw) if raw else 1_700_000
        except ValueError:
            return 1_700_000

    @staticmethod
    def get_cams_stage_s3_bucket() -> str | None:
        """Private S3 bucket for staging CAS PDFs over the multipart cap:
        uploaded under an unguessable key, casparser fetches a ~10-min
        presigned GET URL (their ``pdf_url`` mode has no size limit), object
        deleted right after the parse. Credentials/region come from boto3's
        default chain (env vars or the EC2 instance role). Unset → large
        files fall back to multipart and surface the too-large error."""
        v = (_getenv("CAMS_STAGE_S3_BUCKET") or "").strip()
        return v or None

    @staticmethod
    def get_cams_stage_kms_key_id() -> str | None:
        """KMS key for SSE-KMS on staged CAS PDFs (buckets that enforce KMS
        encryption reject plain SSE-S3 puts). NB: presigned GETs of SSE-KMS
        objects need the signing identity to hold ``kms:Decrypt`` on this key,
        not just encrypt/write. Unset → SSE-S3 (AES256)."""
        v = (_getenv("CAMS_STAGE_KMS_KEY_ID") or "").strip()
        return v or None

    @staticmethod
    def get_openai_api_key() -> str | None:
        """OpenAI key for intent fallback, general chat, and market-commentary fallback (trimmed)."""
        v = (_getenv("OPENAI_API_KEY") or "").strip()
        if v:
            return v
        load_dotenv(_backend_dir / ".env", override=False, encoding="utf-8-sig")
        v = (_getenv("OPENAI_API_KEY") or "").strip()
        return v or None

    # -- PostHog LLM observability -----------------------------------------

    @staticmethod
    def get_posthog_api_key() -> str | None:
        """PostHog project token for backend LLM observability (trimmed).

        Same project token the frontend uses (``VITE_PUBLIC_POSTHOG_KEY``);
        unset disables LLM capture entirely."""
        v = (_getenv("POSTHOG_API_KEY") or "").strip()
        return v or None

    @staticmethod
    def get_posthog_host() -> str:
        """PostHog ingestion host. Defaults to US cloud, matching the frontend."""
        return (_getenv("POSTHOG_HOST") or "").strip() or "https://us.i.posthog.com"

    @staticmethod
    def posthog_llm_capture_content() -> bool:
        """Send prompts, completions, and LangGraph state to PostHog.

        Default OFF: production must not ship customer holdings/cashflow data to
        a third party. When false the handler runs in privacy mode — costs,
        tokens, latency, model, trace/span structure, and per-user attribution
        are still captured; only ``$ai_input``, ``$ai_output_choices``,
        ``$ai_input_state`` and ``$ai_output_state`` are redacted.

        Set ``POSTHOG_LLM_CAPTURE_CONTENT=true`` in dev/staging only."""
        raw = (_getenv("POSTHOG_LLM_CAPTURE_CONTENT") or "").strip().lower()
        return raw in {"1", "true", "yes", "on"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
