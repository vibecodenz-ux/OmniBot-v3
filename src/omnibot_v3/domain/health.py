"""Operational health, readiness, and degraded-state models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from omnibot_v3.domain.runtime import Market


def utc_now() -> datetime:
    return datetime.now(UTC)


class RuntimeHealthState(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


@dataclass(frozen=True, slots=True)
class RuntimeCadencePolicy:
    max_snapshot_age_seconds: int = 60
    max_worker_validation_age_seconds: int = 300
    max_worker_health_age_seconds: int = 60
    max_worker_reconciliation_age_seconds: int = 60
    max_broker_health_age_seconds: int = 60


@dataclass(frozen=True, slots=True)
class MarketHealthReport:
    market: Market
    state: RuntimeHealthState
    ready: bool
    reason: str


@dataclass(frozen=True, slots=True)
class RuntimeHealthReport:
    state: RuntimeHealthState
    ready: bool
    market_reports: tuple[MarketHealthReport, ...]
    checked_at: datetime = field(default_factory=utc_now)