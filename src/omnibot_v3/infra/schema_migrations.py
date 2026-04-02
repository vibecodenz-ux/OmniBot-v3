"""Schema migration metadata and versioning support for PostgreSQL-backed stores."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256

from omnibot_v3.domain.data_lifecycle import RuntimeEventRetentionPolicy
from omnibot_v3.infra.postgres_runtime_store import (
    PostgresRuntimeStoreConfig,
    SqlExecutor,
    build_runtime_event_archive_schema_sql,
    build_runtime_store_schema_sql,
)


@dataclass(frozen=True, slots=True)
class PostgresSchemaMigrationConfig:
    schema_name: str = "omnibot"
    migration_table: str = "schema_migrations"


@dataclass(frozen=True, slots=True)
class SchemaMigration:
    version: str
    description: str
    sql: str

    @property
    def checksum(self) -> str:
        return sha256(self.sql.encode("utf-8")).hexdigest()


def build_schema_migration_sql(config: PostgresSchemaMigrationConfig) -> str:
    return f"""
CREATE SCHEMA IF NOT EXISTS {config.schema_name};

CREATE TABLE IF NOT EXISTS {config.schema_name}.{config.migration_table} (
    version TEXT PRIMARY KEY,
    description TEXT NOT NULL,
    checksum TEXT NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_{config.migration_table}_applied_at
ON {config.schema_name}.{config.migration_table} (applied_at);
""".strip()


def build_initial_operational_schema_sql(config: PostgresRuntimeStoreConfig) -> str:
    return (
        build_runtime_store_schema_sql(config)
        + "\n\n"
        + f"""
CREATE TABLE IF NOT EXISTS {config.schema_name}.users (
    user_id TEXT PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    last_login_at TIMESTAMPTZ NULL
);

CREATE TABLE IF NOT EXISTS {config.schema_name}.sessions (
    session_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES {config.schema_name}.users (user_id) ON DELETE CASCADE,
    csrf_token_hash TEXT NOT NULL,
    client_ip TEXT NULL,
    user_agent TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    last_seen_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ NULL
);

CREATE INDEX IF NOT EXISTS ix_sessions_user_id
ON {config.schema_name}.sessions (user_id, expires_at);

CREATE TABLE IF NOT EXISTS {config.schema_name}.broker_connections (
    connection_id TEXT PRIMARY KEY,
    market TEXT NOT NULL UNIQUE,
    broker_name TEXT NOT NULL,
    environment TEXT NOT NULL,
    account_reference TEXT NULL,
    status TEXT NOT NULL,
    capabilities JSONB NOT NULL,
    last_validated_at TIMESTAMPTZ NULL,
    last_healthy_at TIMESTAMPTZ NULL,
    last_error TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS {config.schema_name}.reconciliation_runs (
    reconciliation_run_id TEXT PRIMARY KEY,
    market TEXT NOT NULL,
    requested_by TEXT NULL REFERENCES {config.schema_name}.users (user_id) ON DELETE SET NULL,
    status TEXT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ NULL,
    broker_snapshot JSONB NOT NULL,
    notes JSONB NOT NULL DEFAULT '{{}}'::jsonb
);

CREATE INDEX IF NOT EXISTS ix_reconciliation_runs_market_started_at
ON {config.schema_name}.reconciliation_runs (market, started_at);

CREATE TABLE IF NOT EXISTS {config.schema_name}.strategy_runs (
    strategy_run_id TEXT PRIMARY KEY,
    market TEXT NOT NULL,
    strategy_name TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    profile_name TEXT NOT NULL,
    status TEXT NOT NULL,
    configuration JSONB NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    ended_at TIMESTAMPTZ NULL,
    notes JSONB NOT NULL DEFAULT '{{}}'::jsonb
);

CREATE INDEX IF NOT EXISTS ix_strategy_runs_market_started_at
ON {config.schema_name}.strategy_runs (market, started_at);

CREATE TABLE IF NOT EXISTS {config.schema_name}.orders (
    order_id TEXT PRIMARY KEY,
    broker_connection_id TEXT NULL REFERENCES {config.schema_name}.broker_connections (connection_id) ON DELETE SET NULL,
    strategy_run_id TEXT NULL REFERENCES {config.schema_name}.strategy_runs (strategy_run_id) ON DELETE SET NULL,
    market TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    order_type TEXT NOT NULL,
    time_in_force TEXT NOT NULL,
    status TEXT NOT NULL,
    quantity NUMERIC(28, 10) NOT NULL,
    filled_quantity NUMERIC(28, 10) NOT NULL DEFAULT 0,
    limit_price NUMERIC(28, 10) NULL,
    average_fill_price NUMERIC(28, 10) NULL,
    submitted_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb
);

CREATE INDEX IF NOT EXISTS ix_orders_market_submitted_at
ON {config.schema_name}.orders (market, submitted_at);

CREATE TABLE IF NOT EXISTS {config.schema_name}.fills (
    fill_id TEXT PRIMARY KEY,
    order_id TEXT NOT NULL REFERENCES {config.schema_name}.orders (order_id) ON DELETE CASCADE,
    market TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    quantity NUMERIC(28, 10) NOT NULL,
    price NUMERIC(28, 10) NOT NULL,
    commission NUMERIC(28, 10) NOT NULL DEFAULT 0,
    executed_at TIMESTAMPTZ NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb
);

CREATE INDEX IF NOT EXISTS ix_fills_order_id_executed_at
ON {config.schema_name}.fills (order_id, executed_at);

CREATE TABLE IF NOT EXISTS {config.schema_name}.positions (
    position_id TEXT PRIMARY KEY,
    reconciliation_run_id TEXT NULL REFERENCES {config.schema_name}.reconciliation_runs (reconciliation_run_id) ON DELETE SET NULL,
    market TEXT NOT NULL,
    symbol TEXT NOT NULL,
    quantity NUMERIC(28, 10) NOT NULL,
    average_price NUMERIC(28, 10) NOT NULL,
    market_price NUMERIC(28, 10) NOT NULL,
    as_of TIMESTAMPTZ NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb
);

CREATE INDEX IF NOT EXISTS ix_positions_market_symbol_as_of
ON {config.schema_name}.positions (market, symbol, as_of);

CREATE TABLE IF NOT EXISTS {config.schema_name}.trades (
    trade_id TEXT PRIMARY KEY,
    strategy_run_id TEXT NULL REFERENCES {config.schema_name}.strategy_runs (strategy_run_id) ON DELETE SET NULL,
    market TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    quantity NUMERIC(28, 10) NOT NULL,
    entry_price NUMERIC(28, 10) NOT NULL,
    exit_price NUMERIC(28, 10) NOT NULL,
    fees NUMERIC(28, 10) NOT NULL DEFAULT 0,
    opened_at TIMESTAMPTZ NOT NULL,
    closed_at TIMESTAMPTZ NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb
);

CREATE INDEX IF NOT EXISTS ix_trades_market_closed_at
ON {config.schema_name}.trades (market, closed_at);

CREATE TABLE IF NOT EXISTS {config.schema_name}.audit_events (
    audit_event_id BIGSERIAL PRIMARY KEY,
    actor_user_id TEXT NULL REFERENCES {config.schema_name}.users (user_id) ON DELETE SET NULL,
    market TEXT NULL,
    category TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload JSONB NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_audit_events_occurred_at
ON {config.schema_name}.audit_events (occurred_at);

CREATE TABLE IF NOT EXISTS {config.schema_name}.health_checks (
    health_check_id BIGSERIAL PRIMARY KEY,
    component TEXT NOT NULL,
    market TEXT NULL,
    status TEXT NOT NULL,
    detail TEXT NULL,
    payload JSONB NOT NULL DEFAULT '{{}}'::jsonb,
    checked_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_health_checks_component_checked_at
ON {config.schema_name}.health_checks (component, checked_at);
""".strip()
    )


def default_schema_migrations(
    config: PostgresRuntimeStoreConfig,
    policy: RuntimeEventRetentionPolicy,
) -> tuple[SchemaMigration, ...]:
    return (
        SchemaMigration(
            version="0001",
            description="initial operational truth schema",
            sql=build_initial_operational_schema_sql(config),
        ),
        SchemaMigration(
            version="0002",
            description="runtime event archive schema",
            sql=build_runtime_event_archive_schema_sql(config, policy),
        ),
    )


@dataclass(slots=True)
class PostgresSchemaMigrator:
    config: PostgresSchemaMigrationConfig
    executor: SqlExecutor

    def create_schema(self) -> None:
        self.executor.execute(build_schema_migration_sql(self.config))

    def list_applied_versions(self) -> list[str]:
        rows = self.executor.fetch_all(
            f"SELECT version FROM {self.config.schema_name}.{self.config.migration_table} "
            f"ORDER BY applied_at, version"
        )
        return [str(row["version"]) for row in rows]

    def pending_migrations(
        self, migrations: tuple[SchemaMigration, ...]
    ) -> tuple[SchemaMigration, ...]:
        applied_versions = set(self.list_applied_versions())
        return tuple(
            migration for migration in migrations if migration.version not in applied_versions
        )

    def apply_migration(
        self, migration: SchemaMigration, applied_at: datetime | None = None
    ) -> None:
        timestamp = (applied_at or datetime.now(UTC)).isoformat()
        self.executor.execute(migration.sql)
        self.executor.execute(
            (
                f"INSERT INTO {self.config.schema_name}.{self.config.migration_table} "
                f"(version, description, checksum, applied_at) "
                f"VALUES (%(version)s, %(description)s, %(checksum)s, %(applied_at)s) "
                f"ON CONFLICT (version) DO UPDATE SET "
                f"description = EXCLUDED.description, checksum = EXCLUDED.checksum, applied_at = EXCLUDED.applied_at"
            ),
            {
                "version": migration.version,
                "description": migration.description,
                "checksum": migration.checksum,
                "applied_at": timestamp,
            },
        )
