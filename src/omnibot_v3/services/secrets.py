"""Secret storage policy helpers, lifecycle transitions, and store-backed handling."""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

from omnibot_v3.domain.secrets import (
    SecretBackend,
    SecretLifecycleState,
    SecretMetadata,
    SecretScope,
    SecretStoragePolicy,
)


def utc_now() -> datetime:
    return datetime.now(UTC)


def _mask_reference(reference: str) -> str:
    trimmed = reference.strip()
    if len(trimmed) <= 4:
        return "*" * len(trimmed)
    return f"{trimmed[:2]}***{trimmed[-2:]}"


def _normalize_secret_token(secret_id: str) -> str:
    token = []
    for character in secret_id.strip():
        token.append(character.upper() if character.isalnum() else "_")
    normalized = "".join(token).strip("_")
    return normalized or "SECRET"


class SecretAccessError(RuntimeError):
    """Raised when a secret cannot be resolved or updated through its backend."""


class ExternalSecretStore(Protocol):
    def store_secret(self, reference: str, value: str) -> str: ...

    def resolve_secret(self, reference: str) -> str: ...

    def delete_secret(self, reference: str) -> None: ...


@dataclass(frozen=True, slots=True)
class SecretRotationRequest:
    metadata: SecretMetadata
    new_value: str
    new_reference: str | None = None
    validate_after_rotation: bool = True


@dataclass(frozen=True, slots=True)
class SecretRotationResult:
    secret_id: str
    previous_version: int
    metadata: SecretMetadata
    previous_reference_removed: bool
    validation_attempted: bool
    validation_passed: bool
    validation_error: str | None = None


@dataclass(frozen=True, slots=True)
class SecretRotationSummary:
    rotated_at: datetime
    passed: bool
    results: tuple[SecretRotationResult, ...]


@dataclass(frozen=True, slots=True)
class SecretPolicyService:
    policy: SecretStoragePolicy = SecretStoragePolicy()

    def build_metadata(
        self,
        secret_id: str,
        scope: SecretScope,
        reference: str,
        *,
        created_at: datetime | None = None,
    ) -> SecretMetadata:
        backend = self._resolve_backend(scope)
        timestamp = created_at or utc_now()
        next_rotation_due_at = self._default_rotation_due_at(scope, timestamp)
        return SecretMetadata(
            secret_id=secret_id,
            scope=scope,
            backend=backend,
            reference=reference,
            masked_display=_mask_reference(reference),
            rotation_required=scope == SecretScope.BROKER
            and self.policy.require_rotation_for_broker_credentials,
            created_at=timestamp,
            next_rotation_due_at=next_rotation_due_at,
            updated_at=timestamp,
        )

    def mark_validated(
        self,
        metadata: SecretMetadata,
        *,
        validated_at: datetime | None = None,
    ) -> SecretMetadata:
        timestamp = validated_at or utc_now()
        lifecycle_state = metadata.lifecycle_state
        if lifecycle_state != SecretLifecycleState.REVOKED:
            lifecycle_state = SecretLifecycleState.ACTIVE
        return replace(
            metadata,
            lifecycle_state=lifecycle_state,
            last_validated_at=timestamp,
            validation_error=None,
            updated_at=timestamp,
        )

    def mark_validation_failed(
        self,
        metadata: SecretMetadata,
        error_message: str,
        *,
        failed_at: datetime | None = None,
    ) -> SecretMetadata:
        timestamp = failed_at or utc_now()
        return replace(
            metadata,
            lifecycle_state=SecretLifecycleState.INVALID,
            validation_error=error_message,
            updated_at=timestamp,
        )

    def mark_rotation_required(
        self,
        metadata: SecretMetadata,
        *,
        due_at: datetime | None = None,
        flagged_at: datetime | None = None,
    ) -> SecretMetadata:
        timestamp = flagged_at or utc_now()
        return replace(
            metadata,
            lifecycle_state=SecretLifecycleState.ROTATION_REQUIRED,
            next_rotation_due_at=due_at or metadata.next_rotation_due_at or timestamp,
            updated_at=timestamp,
        )

    def rotate_secret(
        self,
        metadata: SecretMetadata,
        new_reference: str,
        *,
        rotated_at: datetime | None = None,
    ) -> SecretMetadata:
        timestamp = rotated_at or utc_now()
        return replace(
            metadata,
            reference=new_reference,
            masked_display=_mask_reference(new_reference),
            lifecycle_state=SecretLifecycleState.PENDING_VALIDATION,
            version=metadata.version + 1,
            last_rotated_at=timestamp,
            last_validated_at=None,
            next_rotation_due_at=self._default_rotation_due_at(metadata.scope, timestamp),
            revoked_at=None,
            validation_error=None,
            updated_at=timestamp,
        )

    def revoke_secret(
        self,
        metadata: SecretMetadata,
        *,
        revoked_at: datetime | None = None,
    ) -> SecretMetadata:
        timestamp = revoked_at or utc_now()
        return replace(
            metadata,
            lifecycle_state=SecretLifecycleState.REVOKED,
            revoked_at=timestamp,
            next_rotation_due_at=None,
            updated_at=timestamp,
        )

    def metadata_view(self, metadata: SecretMetadata) -> dict[str, object]:
        return {
            "secret_id": metadata.secret_id,
            "scope": metadata.scope.value,
            "backend": metadata.backend.value,
            "masked_display": metadata.masked_display,
            "lifecycle_state": metadata.lifecycle_state.value,
            "version": metadata.version,
            "rotation_required": metadata.rotation_required,
            "created_at": metadata.created_at.isoformat(),
            "last_validated_at": self._timestamp_or_none(metadata.last_validated_at),
            "last_rotated_at": self._timestamp_or_none(metadata.last_rotated_at),
            "next_rotation_due_at": self._timestamp_or_none(metadata.next_rotation_due_at),
            "revoked_at": self._timestamp_or_none(metadata.revoked_at),
            "validation_error": metadata.validation_error,
            "updated_at": metadata.updated_at.isoformat(),
        }

    def redact_payload(self, payload: object) -> object:
        if isinstance(payload, Mapping):
            return {
                str(key): self.policy.redacted_value
                if self._is_sensitive_key(str(key))
                else self.redact_payload(value)
                for key, value in payload.items()
            }
        if isinstance(payload, Sequence) and not isinstance(payload, (str, bytes, bytearray)):
            return [self.redact_payload(item) for item in payload]
        return payload

    def _resolve_backend(self, scope: SecretScope) -> SecretBackend:
        if scope == SecretScope.SESSION:
            return SecretBackend.ENVIRONMENT
        return self.policy.default_backend

    def _default_rotation_due_at(self, scope: SecretScope, timestamp: datetime) -> datetime | None:
        if scope != SecretScope.BROKER or not self.policy.require_rotation_for_broker_credentials:
            return None
        return timestamp + timedelta(days=self.policy.broker_rotation_period_days)

    def _is_sensitive_key(self, key: str) -> bool:
        normalized = key.strip().lower().replace("-", "_")
        return any(marker in normalized for marker in self.policy.sensitive_key_markers)

    def _timestamp_or_none(self, value: datetime | None) -> str | None:
        return value.isoformat() if value is not None else None


@dataclass(frozen=True, slots=True)
class SecretStoreService:
    policy_service: SecretPolicyService = SecretPolicyService()
    environment: MutableMapping[str, str] | None = None
    root_directory: Path | None = None
    external_store: ExternalSecretStore | None = None

    @property
    def policy(self) -> SecretStoragePolicy:
        return self.policy_service.policy

    def store_secret(
        self,
        secret_id: str,
        scope: SecretScope,
        value: str,
        *,
        reference: str | None = None,
        existing_metadata: SecretMetadata | None = None,
        timestamp: datetime | None = None,
    ) -> SecretMetadata:
        backend = self.policy_service._resolve_backend(scope)
        stored_at = timestamp or utc_now()
        stored_reference = self._store_value(secret_id, scope, backend, value, reference)
        if existing_metadata is not None:
            return self.policy_service.rotate_secret(
                existing_metadata,
                stored_reference,
                rotated_at=stored_at,
            )
        return self.policy_service.build_metadata(
            secret_id,
            scope,
            stored_reference,
            created_at=stored_at,
        )

    def resolve_secret(self, metadata: SecretMetadata) -> str:
        if metadata.lifecycle_state == SecretLifecycleState.REVOKED:
            raise SecretAccessError(f"Secret '{metadata.secret_id}' has been revoked.")

        if metadata.backend == SecretBackend.ENVIRONMENT:
            environment = self._environment()
            try:
                return environment[metadata.reference]
            except KeyError as error:
                raise SecretAccessError(
                    f"Environment secret '{metadata.reference}' is not available."
                ) from error

        if metadata.backend == SecretBackend.FILESYSTEM:
            try:
                return Path(metadata.reference).read_text(encoding="utf-8")
            except OSError as error:
                raise SecretAccessError(
                    f"Filesystem secret '{metadata.reference}' could not be read."
                ) from error

        external_store = self._external_store()
        try:
            return external_store.resolve_secret(metadata.reference)
        except Exception as error:
            raise SecretAccessError(
                f"External secret '{metadata.reference}' could not be resolved."
            ) from error

    def delete_secret(
        self,
        metadata: SecretMetadata,
        *,
        deleted_at: datetime | None = None,
    ) -> SecretMetadata:
        if metadata.backend == SecretBackend.ENVIRONMENT:
            self._environment().pop(metadata.reference, None)
        elif metadata.backend == SecretBackend.FILESYSTEM:
            path = Path(metadata.reference)
            try:
                path.unlink(missing_ok=True)
            except OSError as error:
                raise SecretAccessError(
                    f"Filesystem secret '{metadata.reference}' could not be deleted."
                ) from error
        else:
            external_store = self._external_store()
            try:
                external_store.delete_secret(metadata.reference)
            except Exception as error:
                raise SecretAccessError(
                    f"External secret '{metadata.reference}' could not be deleted."
                ) from error
        return self.policy_service.revoke_secret(metadata, revoked_at=deleted_at)

    def _store_value(
        self,
        secret_id: str,
        scope: SecretScope,
        backend: SecretBackend,
        value: str,
        reference: str | None,
    ) -> str:
        if backend == SecretBackend.ENVIRONMENT:
            environment_reference = reference or self._default_environment_reference(secret_id)
            self._environment()[environment_reference] = value
            return environment_reference

        if backend == SecretBackend.FILESYSTEM:
            path = self._filesystem_path(secret_id, reference)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(value, encoding="utf-8")
            try:
                path.chmod(self.policy.filesystem_file_mode)
            except OSError:
                pass
            return path.as_posix()

        external_reference = reference or self._default_external_reference(scope, secret_id)
        external_store = self._external_store()
        try:
            return external_store.store_secret(external_reference, value)
        except Exception as error:
            raise SecretAccessError(
                f"External secret '{external_reference}' could not be stored."
            ) from error

    def _environment(self) -> MutableMapping[str, str]:
        if self.environment is None:
            raise SecretAccessError(
                "Environment-backed secret handling requires an environment mapping."
            )
        return self.environment

    def _filesystem_path(self, secret_id: str, reference: str | None) -> Path:
        if reference is not None:
            return Path(reference)
        root_directory = self.root_directory or Path.cwd()
        file_name = f"{_normalize_secret_token(secret_id).lower()}.secret"
        return root_directory / self.policy.filesystem_directory / file_name

    def _default_environment_reference(self, secret_id: str) -> str:
        return f"{self.policy.environment_variable_prefix}{_normalize_secret_token(secret_id)}"

    def _default_external_reference(self, scope: SecretScope, secret_id: str) -> str:
        prefix = self.policy.external_reference_prefix.rstrip("/")
        return f"{prefix}/{scope.value}/{secret_id}"

    def _external_store(self) -> ExternalSecretStore:
        if self.external_store is None:
            raise SecretAccessError(
                "External-store-backed secret handling requires an external secret store adapter."
            )
        return self.external_store


@dataclass(frozen=True, slots=True)
class SecretRotationService:
    store_service: SecretStoreService

    def emergency_rotate(
        self,
        requests: Sequence[SecretRotationRequest],
        *,
        rotated_at: datetime | None = None,
    ) -> SecretRotationSummary:
        timestamp = rotated_at or utc_now()
        results: list[SecretRotationResult] = []

        for request in requests:
            rotated_metadata = self.store_service.store_secret(
                request.metadata.secret_id,
                request.metadata.scope,
                request.new_value,
                reference=request.new_reference,
                existing_metadata=request.metadata,
                timestamp=timestamp,
            )

            previous_reference_removed = False
            if request.metadata.reference != rotated_metadata.reference:
                self.store_service.delete_secret(request.metadata, deleted_at=timestamp)
                previous_reference_removed = True

            validation_attempted = request.validate_after_rotation
            validation_passed = True
            validation_error: str | None = None
            final_metadata = rotated_metadata

            if request.validate_after_rotation:
                try:
                    self.store_service.resolve_secret(rotated_metadata)
                except SecretAccessError as error:
                    validation_passed = False
                    validation_error = str(error)
                    final_metadata = self.store_service.policy_service.mark_validation_failed(
                        rotated_metadata,
                        validation_error,
                        failed_at=timestamp,
                    )
                else:
                    final_metadata = self.store_service.policy_service.mark_validated(
                        rotated_metadata,
                        validated_at=timestamp,
                    )

            results.append(
                SecretRotationResult(
                    secret_id=request.metadata.secret_id,
                    previous_version=request.metadata.version,
                    metadata=final_metadata,
                    previous_reference_removed=previous_reference_removed,
                    validation_attempted=validation_attempted,
                    validation_passed=validation_passed,
                    validation_error=validation_error,
                )
            )

        return SecretRotationSummary(
            rotated_at=timestamp,
            passed=all(
                result.validation_passed or not result.validation_attempted for result in results
            ),
            results=tuple(results),
        )
