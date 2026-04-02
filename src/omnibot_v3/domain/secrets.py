"""Secret storage strategy, lifecycle, and metadata models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum


def utc_now() -> datetime:
    return datetime.now(UTC)


class SecretBackend(StrEnum):
    ENVIRONMENT = "environment"
    FILESYSTEM = "filesystem"
    EXTERNAL_STORE = "external-store"


class SecretScope(StrEnum):
    BROKER = "broker"
    SESSION = "session"
    SYSTEM = "system"


class SecretLifecycleState(StrEnum):
    PENDING_VALIDATION = "pending-validation"
    ACTIVE = "active"
    ROTATION_REQUIRED = "rotation-required"
    INVALID = "invalid"
    REVOKED = "revoked"


@dataclass(frozen=True, slots=True)
class SecretMetadata:
    secret_id: str
    scope: SecretScope
    backend: SecretBackend
    reference: str
    masked_display: str
    lifecycle_state: SecretLifecycleState = SecretLifecycleState.PENDING_VALIDATION
    version: int = 1
    rotation_required: bool = False
    created_at: datetime = field(default_factory=utc_now)
    last_validated_at: datetime | None = None
    last_rotated_at: datetime | None = None
    next_rotation_due_at: datetime | None = None
    revoked_at: datetime | None = None
    validation_error: str | None = None
    updated_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True, slots=True)
class SecretStoragePolicy:
    default_backend: SecretBackend = SecretBackend.FILESYSTEM
    filesystem_directory: str = "secrets"
    filesystem_file_mode: int = 0o600
    environment_variable_prefix: str = "OMNIBOT_SECRET_"
    external_reference_prefix: str = "secret://omnibot/"
    allow_plaintext_env_files: bool = False
    require_rotation_for_broker_credentials: bool = True
    broker_rotation_period_days: int = 90
    redacted_value: str = "[redacted]"
    sensitive_key_markers: tuple[str, ...] = (
        "secret",
        "token",
        "password",
        "passwd",
        "api_key",
        "apikey",
        "client_secret",
        "access_key",
        "private_key",
    )
