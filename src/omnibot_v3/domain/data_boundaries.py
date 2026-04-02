"""Data boundary and storage-tier contracts for operational and cache data."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class DataStorageTier(StrEnum):
    OPERATIONAL = "operational"
    EPHEMERAL_CACHE = "ephemeral-cache"


@dataclass(frozen=True, slots=True)
class DataBoundary:
    name: str
    storage_tier: DataStorageTier
    location: str
    description: str
