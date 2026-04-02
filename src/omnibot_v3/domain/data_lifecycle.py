"""Data retention and archival policies for persisted runtime and research records."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RuntimeEventRetentionPolicy:
    retention_days: int = 30
    archive_schema_name: str = "omnibot_archive"
    archive_event_table: str = "runtime_events"