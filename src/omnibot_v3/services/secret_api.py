"""Application-layer adapter for credential management and secret status views."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from omnibot_v3.domain import SecretMetadata, SecretScope
from omnibot_v3.services.secrets import (
    SecretAccessError,
    SecretRotationRequest,
    SecretRotationService,
    SecretStoreService,
)


class SecretRegistry(Protocol):
    def save(self, metadata: SecretMetadata) -> None:
        """Persist secret metadata."""

    def get(self, secret_id: str) -> SecretMetadata | None:
        """Return secret metadata when it exists."""

    def list_all(self) -> list[SecretMetadata]:
        """Return all stored secret metadata."""

    def delete(self, secret_id: str) -> None:
        """Delete secret metadata."""


class SecretNotFoundError(KeyError):
    """Raised when a requested secret metadata record does not exist."""


@dataclass(frozen=True, slots=True)
class SecretApiService:
    registry: SecretRegistry
    store_service: SecretStoreService
    rotation_service: SecretRotationService

    def list_secret_metadata(self, *, scope: SecretScope | None = None) -> dict[str, object]:
        metadata_items = self.registry.list_all()
        if scope is not None:
            metadata_items = [metadata for metadata in metadata_items if metadata.scope == scope]
        return {
            "secret_count": len(metadata_items),
            "secrets": [
                self.store_service.policy_service.metadata_view(metadata)
                for metadata in metadata_items
            ],
        }

    def get_secret_metadata(self, secret_id: str) -> dict[str, object]:
        metadata = self._require_metadata(secret_id)
        return self.store_service.policy_service.metadata_view(metadata)

    def upsert_secret(
        self,
        *,
        secret_id: str,
        scope: SecretScope,
        value: str,
        reference: str | None = None,
        validate_after_store: bool = True,
        stored_at: datetime | None = None,
    ) -> dict[str, object]:
        existing = self.registry.get(secret_id)
        metadata = self.store_service.store_secret(
            secret_id,
            scope,
            value,
            reference=reference,
            existing_metadata=existing,
            timestamp=stored_at,
        )
        final_metadata = self._validate_metadata(metadata) if validate_after_store else metadata
        self.registry.save(final_metadata)
        return self.store_service.policy_service.metadata_view(final_metadata)

    def validate_secret(self, secret_id: str, *, validated_at: datetime | None = None) -> dict[str, object]:
        metadata = self._require_metadata(secret_id)
        timestamp = validated_at or datetime.now()
        try:
            self.store_service.resolve_secret(metadata)
        except SecretAccessError as error:
            final_metadata = self.store_service.policy_service.mark_validation_failed(
                metadata,
                str(error),
                failed_at=timestamp,
            )
        else:
            final_metadata = self.store_service.policy_service.mark_validated(
                metadata,
                validated_at=timestamp,
            )
        self.registry.save(final_metadata)
        return self.store_service.policy_service.metadata_view(final_metadata)

    def rotate_secret(
        self,
        *,
        secret_id: str,
        new_value: str,
        new_reference: str | None = None,
        validate_after_rotation: bool = True,
        rotated_at: datetime | None = None,
    ) -> dict[str, object]:
        metadata = self._require_metadata(secret_id)
        summary = self.rotation_service.emergency_rotate(
            (
                SecretRotationRequest(
                    metadata=metadata,
                    new_value=new_value,
                    new_reference=new_reference,
                    validate_after_rotation=validate_after_rotation,
                ),
            ),
            rotated_at=rotated_at,
        )
        result = summary.results[0]
        self.registry.save(result.metadata)
        return {
            "rotated_at": summary.rotated_at.isoformat(),
            "passed": summary.passed,
            "result": {
                "secret_id": result.secret_id,
                "previous_version": result.previous_version,
                "metadata": self.store_service.policy_service.metadata_view(result.metadata),
                "previous_reference_removed": result.previous_reference_removed,
                "validation_attempted": result.validation_attempted,
                "validation_passed": result.validation_passed,
                "validation_error": result.validation_error,
            },
        }

    def revoke_secret(self, secret_id: str, *, deleted_at: datetime | None = None) -> dict[str, object]:
        metadata = self._require_metadata(secret_id)
        revoked = self.store_service.delete_secret(metadata, deleted_at=deleted_at)
        self.registry.save(revoked)
        return self.store_service.policy_service.metadata_view(revoked)

    def _require_metadata(self, secret_id: str) -> SecretMetadata:
        metadata = self.registry.get(secret_id)
        if metadata is None:
            raise SecretNotFoundError(secret_id)
        return metadata

    def _validate_metadata(self, metadata: SecretMetadata) -> SecretMetadata:
        try:
            self.store_service.resolve_secret(metadata)
        except SecretAccessError as error:
            return self.store_service.policy_service.mark_validation_failed(metadata, str(error))
        return self.store_service.policy_service.mark_validated(metadata)