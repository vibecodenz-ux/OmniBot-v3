"""In-memory and JSON-backed secret metadata registries."""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile

from omnibot_v3.domain import SecretMetadata
from omnibot_v3.domain.secrets import SecretBackend, SecretLifecycleState, SecretScope
from omnibot_v3.services.secret_api import SecretRegistry


@dataclass(slots=True)
class InMemorySecretRegistry(SecretRegistry):
    entries: dict[str, SecretMetadata] = field(default_factory=dict)

    def save(self, metadata: SecretMetadata) -> None:
        self.entries[metadata.secret_id] = metadata

    def get(self, secret_id: str) -> SecretMetadata | None:
        return self.entries.get(secret_id)

    def list_all(self) -> list[SecretMetadata]:
        return list(self.entries.values())

    def delete(self, secret_id: str) -> None:
        self.entries.pop(secret_id, None)


@dataclass(frozen=True, slots=True)
class JsonFileSecretRegistry(SecretRegistry):
    path: Path

    def save(self, metadata: SecretMetadata) -> None:
        entries = {item.secret_id: item for item in self.list_all()}
        entries[metadata.secret_id] = metadata
        self._write(entries.values())

    def get(self, secret_id: str) -> SecretMetadata | None:
        return next((item for item in self.list_all() if item.secret_id == secret_id), None)

    def list_all(self) -> list[SecretMetadata]:
        if not self.path.is_file():
            return []

        payload = json.loads(self.path.read_text(encoding="utf-8"))
        raw_entries = payload.get("entries", [])
        if not isinstance(raw_entries, list):
            raise ValueError("Secret registry file has an invalid entries payload.")
        return [_metadata_from_payload(item) for item in raw_entries]

    def delete(self, secret_id: str) -> None:
        entries = [item for item in self.list_all() if item.secret_id != secret_id]
        self._write(entries)

    def _write(self, entries: Iterable[SecretMetadata]) -> None:
        normalized_entries = sorted(
            list(entries),
            key=lambda item: item.secret_id,
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "entries": [_metadata_to_payload(entry) for entry in normalized_entries],
        }
        with NamedTemporaryFile("w", encoding="utf-8", dir=self.path.parent, delete=False) as handle:
            temp_path = Path(handle.name)
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.flush()
        temp_path.replace(self.path)


def _metadata_from_payload(payload: object) -> SecretMetadata:
    if not isinstance(payload, dict):
        raise ValueError("Secret registry entry must be an object.")
    return SecretMetadata(
        secret_id=str(payload["secret_id"]),
        scope=SecretScope(str(payload["scope"])),
        backend=SecretBackend(str(payload["backend"])),
        reference=str(payload["reference"]),
        masked_display=str(payload["masked_display"]),
        lifecycle_state=SecretLifecycleState(str(payload.get("lifecycle_state", SecretLifecycleState.PENDING_VALIDATION.value))),
        version=int(payload.get("version", 1)),
        rotation_required=bool(payload.get("rotation_required", False)),
        created_at=_parse_datetime(payload.get("created_at")),
        last_validated_at=_parse_optional_datetime(payload.get("last_validated_at")),
        last_rotated_at=_parse_optional_datetime(payload.get("last_rotated_at")),
        next_rotation_due_at=_parse_optional_datetime(payload.get("next_rotation_due_at")),
        revoked_at=_parse_optional_datetime(payload.get("revoked_at")),
        validation_error=_optional_string(payload.get("validation_error")),
        updated_at=_parse_datetime(payload.get("updated_at")),
    )


def _metadata_to_payload(metadata: SecretMetadata) -> dict[str, object]:
    return {
        "secret_id": metadata.secret_id,
        "scope": metadata.scope.value,
        "backend": metadata.backend.value,
        "reference": metadata.reference,
        "masked_display": metadata.masked_display,
        "lifecycle_state": metadata.lifecycle_state.value,
        "version": metadata.version,
        "rotation_required": metadata.rotation_required,
        "created_at": metadata.created_at.isoformat(),
        "last_validated_at": _optional_timestamp(metadata.last_validated_at),
        "last_rotated_at": _optional_timestamp(metadata.last_rotated_at),
        "next_rotation_due_at": _optional_timestamp(metadata.next_rotation_due_at),
        "revoked_at": _optional_timestamp(metadata.revoked_at),
        "validation_error": metadata.validation_error,
        "updated_at": metadata.updated_at.isoformat(),
    }


def _parse_datetime(value: object) -> datetime:
    if value is None:
        raise ValueError("Secret registry timestamp is required.")
    return datetime.fromisoformat(str(value))


def _parse_optional_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(str(value))


def _optional_timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    return str(value)