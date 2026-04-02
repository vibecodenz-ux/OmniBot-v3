"""Linux deployment preflight domain models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum


def utc_now() -> datetime:
    return datetime.now(UTC)


class PreflightCheckSeverity(StrEnum):
    INFO = "info"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class PathPermissionRule:
    path: str
    max_mode: int


@dataclass(frozen=True, slots=True)
class PreflightCheckResult:
    name: str
    passed: bool
    severity: PreflightCheckSeverity
    message: str


@dataclass(frozen=True, slots=True)
class LinuxPreflightPolicy:
    minimum_python_version: tuple[int, int] = (3, 11)
    min_free_disk_bytes: int = 1_073_741_824
    required_commands: tuple[str, ...] = ("bash", "systemctl", "tar")
    required_writable_directories: tuple[str, ...] = ("data", "logs", "secrets")
    required_ports_available: tuple[int, ...] = (8000,)
    required_resolvable_hosts: tuple[str, ...] = ("localhost",)
    permission_rules: tuple[PathPermissionRule, ...] = field(
        default_factory=lambda: (
            PathPermissionRule(path="secrets", max_mode=0o700),
            PathPermissionRule(path="logs", max_mode=0o755),
            PathPermissionRule(path="data", max_mode=0o755),
        )
    )


@dataclass(frozen=True, slots=True)
class LinuxPreflightSnapshot:
    platform: str
    python_version: tuple[int, int, int]
    available_commands: frozenset[str]
    free_disk_bytes: int
    writable_directories: dict[str, bool]
    port_available: dict[int, bool]
    resolvable_hosts: dict[str, bool]
    permission_modes: dict[str, int | None]
    checked_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True, slots=True)
class LinuxPreflightReport:
    passed: bool
    checks: tuple[PreflightCheckResult, ...]
    checked_at: datetime = field(default_factory=utc_now)
