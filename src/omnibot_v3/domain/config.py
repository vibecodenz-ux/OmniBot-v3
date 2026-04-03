"""Environment configuration loading strategy for OmniBot v3.

Configuration is resolved in *precedence order* (highest first):

1. Explicit override values passed directly to the loader (for testing).
2. OS environment variables.
3. A plain-text ``.env``-style file (only when ``allow_env_file`` is ``True``).
4. Hardcoded defaults baked into the domain models.

Loading a ``.env`` file is *opt-in* and disabled by default in keeping with the
``SecretStoragePolicy.allow_plaintext_env_files = False`` policy.  Secrets with
``SecretScope.BROKER`` scope must **never** be sourced from a plain-text env file
even when ``allow_env_file=True``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path


class OmnibotEnvironment(StrEnum):
    """Deployment environment the bot is running in."""

    DEVELOPMENT = "development"
    CI = "ci"
    STAGING = "staging"
    PRODUCTION = "production"


class LogLevel(StrEnum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class CookieSameSite(StrEnum):
    STRICT = "strict"
    LAX = "lax"
    NONE = "none"


@dataclass(frozen=True, slots=True)
class DatabaseConfig:
    """Resolved database connection parameters (no passwords)."""

    dsn: str = "postgresql://omnibot:omnibot@localhost:5432/omnibot"
    pool_min_size: int = 2
    pool_max_size: int = 10
    connect_timeout_seconds: int = 10


@dataclass(frozen=True, slots=True)
class AuthConfig:
    admin_username: str = "admin"
    session_cookie_name: str = "omnibot_session"
    csrf_header_name: str = "X-CSRF-Token"
    session_idle_timeout_seconds: int = 900
    session_absolute_timeout_seconds: int = 28_800
    session_cookie_secure: bool = False
    session_cookie_samesite: CookieSameSite = CookieSameSite.STRICT
    allowed_origin: str | None = None


@dataclass(frozen=True, slots=True)
class AppConfig:
    """Fully-resolved application configuration.

    All fields carry safe defaults suitable for local development.
    No secrets (API keys, passwords) are stored here; those are
    handled by ``SecretStoragePolicy`` / ``SecretPolicyService``.
    """

    environment: OmnibotEnvironment = OmnibotEnvironment.DEVELOPMENT
    log_level: LogLevel = LogLevel.INFO
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    auth: AuthConfig = field(default_factory=AuthConfig)
    # Broker-specific settings — resolved keys only, no credentials.
    broker_paper_trading: bool = True
    # Cadence settings (seconds).
    portfolio_snapshot_interval_seconds: int = 60
    health_check_interval_seconds: int = 30
    # Data directories.
    data_root: str = "data"
    secrets_directory: str = "secrets"
    dashboard_host: str = "127.0.0.1"
    dashboard_port: int = 8000
    update_repo: str = "vibecodenz-ux/OmniBot-v3"
    update_branch: str = "main"
    update_metadata_url: str | None = None
    update_archive_url: str | None = None
    update_install_extras: str = "api"
    systemd_service_name: str = "omnibot-v3"


@dataclass(frozen=True, slots=True)
class EnvFileConfig:
    """Policy for loading a plain-text .env file.

    Disabled by default to respect ``SecretStoragePolicy``.
    """

    allow_env_file: bool = False
    env_file_path: str = ".env"


# ---------------------------------------------------------------------------
# Internal parsing helpers
# ---------------------------------------------------------------------------


def _parse_int(raw: str, default: int) -> int:
    try:
        return int(raw)
    except (ValueError, TypeError):
        return default


def _parse_bool(raw: str, default: bool) -> bool:
    if raw.lower() in {"1", "true", "yes", "on"}:
        return True
    if raw.lower() in {"0", "false", "no", "off"}:
        return False
    return default


def _parse_env_file(path: Path) -> dict[str, str]:
    """Parse a minimal ``.env`` file into a dict.

    Rules:
    - Lines starting with ``#`` or blank lines are ignored.
    - Only ``KEY=VALUE`` pairs are accepted.
    - Inline comments (``# …``) are stripped from values.
    - Surrounding whitespace and matching quotes are stripped from values.
    """
    result: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        # Strip inline comment.
        if "#" in value:
            value = value[: value.index("#")]
        value = value.strip().strip("\"'")
        if key:
            result[key] = value
    return result


# ---------------------------------------------------------------------------
# Public loader
# ---------------------------------------------------------------------------


def load_config(
    *,
    env_file_config: EnvFileConfig | None = None,
    overrides: dict[str, str] | None = None,
    _env_source: dict[str, str] | None = None,
) -> AppConfig:
    """Build an ``AppConfig`` from the environment.

    Parameters
    ----------
    env_file_config:
        Controls whether and which ``.env`` file is loaded.
        Defaults to ``EnvFileConfig()`` (disabled).
    overrides:
        Explicit string key→value pairs that take the highest precedence.
        Intended for testing; mirrors the ``KEY=VALUE`` env-var naming.
    _env_source:
        Internal test hook: substitute for ``os.environ``.
        Production callers must not pass this.
    """
    import os

    if env_file_config is None:
        env_file_config = EnvFileConfig()

    env: dict[str, str] = dict(_env_source if _env_source is not None else os.environ)

    # Layer .env file beneath actual env vars (lower precedence).
    if env_file_config.allow_env_file:
        env_path = Path(env_file_config.env_file_path)
        if env_path.is_file():
            file_vars = _parse_env_file(env_path)
            for k, v in file_vars.items():
                env.setdefault(k, v)

    # Apply explicit overrides with highest precedence.
    if overrides:
        env.update(overrides)

    # --- Resolve each field ---
    raw_env = env.get("OMNIBOT_ENV", OmnibotEnvironment.DEVELOPMENT)
    try:
        environment = OmnibotEnvironment(raw_env.lower())
    except ValueError:
        environment = OmnibotEnvironment.DEVELOPMENT

    raw_log = env.get("OMNIBOT_LOG_LEVEL", LogLevel.INFO)
    try:
        log_level = LogLevel(raw_log.lower())
    except ValueError:
        log_level = LogLevel.INFO

    raw_samesite = env.get("OMNIBOT_SESSION_COOKIE_SAMESITE", CookieSameSite.STRICT)
    try:
        session_cookie_samesite = CookieSameSite(str(raw_samesite).lower())
    except ValueError:
        session_cookie_samesite = CookieSameSite.STRICT

    auth = AuthConfig(
        admin_username=env.get("OMNIBOT_ADMIN_USERNAME", "admin"),
        session_cookie_name=env.get("OMNIBOT_SESSION_COOKIE_NAME", "omnibot_session"),
        csrf_header_name=env.get("OMNIBOT_CSRF_HEADER_NAME", "X-CSRF-Token"),
        session_idle_timeout_seconds=_parse_int(
            env.get("OMNIBOT_SESSION_IDLE_TIMEOUT", ""),
            900,
        ),
        session_absolute_timeout_seconds=_parse_int(
            env.get("OMNIBOT_SESSION_ABSOLUTE_TIMEOUT", ""),
            28_800,
        ),
        session_cookie_secure=_parse_bool(
            env.get(
                "OMNIBOT_SESSION_COOKIE_SECURE",
                "true" if environment is OmnibotEnvironment.PRODUCTION else "false",
            ),
            environment is OmnibotEnvironment.PRODUCTION,
        ),
        session_cookie_samesite=session_cookie_samesite,
        allowed_origin=env.get("OMNIBOT_ALLOWED_ORIGIN") or None,
    )

    database = DatabaseConfig(
        dsn=env.get("OMNIBOT_DB_DSN", "postgresql://omnibot:omnibot@localhost:5432/omnibot"),
        pool_min_size=_parse_int(env.get("OMNIBOT_DB_POOL_MIN", ""), 2),
        pool_max_size=_parse_int(env.get("OMNIBOT_DB_POOL_MAX", ""), 10),
        connect_timeout_seconds=_parse_int(env.get("OMNIBOT_DB_CONNECT_TIMEOUT", ""), 10),
    )

    broker_paper_trading = _parse_bool(
        env.get("OMNIBOT_BROKER_PAPER_TRADING", "true"), True
    )
    portfolio_snapshot_interval_seconds = _parse_int(
        env.get("OMNIBOT_PORTFOLIO_SNAPSHOT_INTERVAL", ""),
        60,
    )
    health_check_interval_seconds = _parse_int(
        env.get("OMNIBOT_HEALTH_CHECK_INTERVAL", ""),
        30,
    )
    data_root = env.get("OMNIBOT_DATA_ROOT", "data")
    secrets_directory = env.get("OMNIBOT_SECRETS_DIR", "secrets")
    dashboard_host = env.get("OMNIBOT_BIND_HOST", "127.0.0.1")
    dashboard_port = _parse_int(env.get("OMNIBOT_PORT", ""), 8000)
    update_repo = env.get("OMNIBOT_UPDATE_REPO", "vibecodenz-ux/OmniBot-v3")
    update_branch = env.get("OMNIBOT_UPDATE_BRANCH", "main")
    update_metadata_url = env.get("OMNIBOT_UPDATE_METADATA_URL") or None
    update_archive_url = env.get("OMNIBOT_UPDATE_ARCHIVE_URL") or None
    default_update_extras = "api,postgres" if os.name != "nt" else "api"
    update_install_extras = env.get("OMNIBOT_UPDATE_EXTRAS", default_update_extras)
    systemd_service_name = env.get("OMNIBOT_SYSTEMD_SERVICE_NAME", "omnibot-v3")

    return AppConfig(
        environment=environment,
        log_level=log_level,
        database=database,
        auth=auth,
        broker_paper_trading=broker_paper_trading,
        portfolio_snapshot_interval_seconds=portfolio_snapshot_interval_seconds,
        health_check_interval_seconds=health_check_interval_seconds,
        data_root=data_root,
        secrets_directory=secrets_directory,
        dashboard_host=dashboard_host,
        dashboard_port=dashboard_port,
        update_repo=update_repo,
        update_branch=update_branch,
        update_metadata_url=update_metadata_url,
        update_archive_url=update_archive_url,
        update_install_extras=update_install_extras,
        systemd_service_name=systemd_service_name,
    )
