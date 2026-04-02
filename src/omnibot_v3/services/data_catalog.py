"""Canonical data-boundary catalog for operational and cache data."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath

from omnibot_v3.domain.data_boundaries import (
    DataBoundary,
    DataStorageTier,
)


@dataclass(frozen=True, slots=True)
class DataCatalog:
    root_directory: PurePosixPath = PurePosixPath("data")
    cache_directory: PurePosixPath = PurePosixPath("data/cache")
    runtime_schema_name: str = "omnibot"
    archive_schema_name: str = "omnibot_archive"

    def standard_boundaries(self) -> tuple[DataBoundary, ...]:
        return (
            DataBoundary(
                name="operational-truth",
                storage_tier=DataStorageTier.OPERATIONAL,
                location=f"postgresql schemas: {self.runtime_schema_name}, {self.archive_schema_name}",
                description="Runtime state, orders, fills, audit events, and recovery-critical records.",
            ),
            DataBoundary(
                name="ephemeral-cache",
                storage_tier=DataStorageTier.EPHEMERAL_CACHE,
                location=self.cache_directory.as_posix(),
                description="Rebuildable caches, temporary materializations, and non-authoritative local accelerators.",
            ),
        )