"""Backup and restore planning helpers for PostgreSQL-backed OmniBot data."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


def _linux_path(path: Path) -> str:
    return path.as_posix()


@dataclass(frozen=True, slots=True)
class PostgresBackupConfig:
    database_url: str
    output_directory: Path
    active_schema_name: str = "omnibot"
    archive_schema_name: str = "omnibot_archive"
    backup_prefix: str = "omnibot"


@dataclass(frozen=True, slots=True)
class BackupPlan:
    backup_file: Path
    manifest_file: Path
    command: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class BackupManifest:
    created_at: datetime
    backup_file: Path
    manifest_file: Path
    command: tuple[str, ...]
    active_schema_name: str
    archive_schema_name: str


@dataclass(frozen=True, slots=True)
class RestorePlan:
    backup_file: Path
    command: tuple[str, ...]
    validation_queries: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RestoreValidationReport:
    backup_file: Path
    command: tuple[str, ...]
    validation_queries: tuple[str, ...]
    generated_at: datetime


def build_backup_plan(config: PostgresBackupConfig, timestamp: datetime | None = None) -> BackupPlan:
    created_at = timestamp or datetime.now(UTC)
    suffix = created_at.strftime("%Y%m%dT%H%M%SZ")
    backup_file = config.output_directory / f"{config.backup_prefix}-{suffix}.dump"
    manifest_file = config.output_directory / f"{config.backup_prefix}-{suffix}.manifest.json"
    command = (
        "pg_dump",
        f"--dbname={config.database_url}",
        "--format=custom",
        f"--schema={config.active_schema_name}",
        f"--schema={config.archive_schema_name}",
        f"--file={_linux_path(backup_file)}",
    )
    return BackupPlan(backup_file=backup_file, manifest_file=manifest_file, command=command)


def build_restore_plan(config: PostgresBackupConfig, backup_file: Path) -> RestorePlan:
    command = (
        "pg_restore",
        f"--dbname={config.database_url}",
        "--clean",
        "--if-exists",
        _linux_path(backup_file),
    )
    validation_queries = (
        f"SELECT COUNT(*) FROM {config.active_schema_name}.schema_migrations;",
        f"SELECT COUNT(*) FROM {config.active_schema_name}.market_runtime_snapshots;",
        f"SELECT COUNT(*) FROM {config.archive_schema_name}.runtime_events;",
    )
    return RestorePlan(backup_file=backup_file, command=command, validation_queries=validation_queries)


def build_backup_manifest(
    config: PostgresBackupConfig,
    plan: BackupPlan,
    created_at: datetime | None = None,
) -> BackupManifest:
    return BackupManifest(
        created_at=created_at or datetime.now(UTC),
        backup_file=plan.backup_file,
        manifest_file=plan.manifest_file,
        command=plan.command,
        active_schema_name=config.active_schema_name,
        archive_schema_name=config.archive_schema_name,
    )


def build_restore_validation_report(
    config: PostgresBackupConfig,
    backup_file: Path,
    generated_at: datetime | None = None,
) -> RestoreValidationReport:
    plan = build_restore_plan(config, backup_file)
    return RestoreValidationReport(
        backup_file=plan.backup_file,
        command=plan.command,
        validation_queries=plan.validation_queries,
        generated_at=generated_at or datetime.now(UTC),
    )


def backup_manifest_to_dict(manifest: BackupManifest) -> dict[str, object]:
    return {
        "created_at": manifest.created_at.isoformat(),
        "backup_file": _linux_path(manifest.backup_file),
        "manifest_file": _linux_path(manifest.manifest_file),
        "command": list(manifest.command),
        "active_schema_name": manifest.active_schema_name,
        "archive_schema_name": manifest.archive_schema_name,
    }


def restore_validation_report_to_dict(report: RestoreValidationReport) -> dict[str, object]:
    return {
        "generated_at": report.generated_at.isoformat(),
        "backup_file": _linux_path(report.backup_file),
        "command": list(report.command),
        "validation_queries": list(report.validation_queries),
    }