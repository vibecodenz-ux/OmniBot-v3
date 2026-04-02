"""In-memory settings persistence adapter for development and tests."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from omnibot_v3.domain.config import AppConfig
from omnibot_v3.services.settings_api import SettingsSnapshot, SettingsStore


@dataclass(slots=True)
class InMemorySettingsStore(SettingsStore):
    snapshot: SettingsSnapshot

    def __init__(self, config: AppConfig, updated_at: datetime | None = None) -> None:
        self.snapshot = SettingsSnapshot(
            config=config,
            updated_at=updated_at or datetime.now(UTC),
        )

    def load(self) -> SettingsSnapshot:
        return self.snapshot

    def save(self, snapshot: SettingsSnapshot) -> None:
        self.snapshot = snapshot