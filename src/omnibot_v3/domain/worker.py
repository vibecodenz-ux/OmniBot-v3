"""Market worker configuration and status models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from omnibot_v3.domain.broker import BrokerCapability, BrokerEnvironment
from omnibot_v3.domain.runtime import Market


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class MarketWorkerSettings:
    market: Market
    environment: BrokerEnvironment = BrokerEnvironment.SANDBOX
    broker_adapter_name: str = "mock-broker"
    symbols: tuple[str, ...] = ()
    allow_live_execution: bool = False
    poll_interval_seconds: int = 5


@dataclass(frozen=True, slots=True)
class MarketWorkerValidationResult:
    market: Market
    valid: bool
    errors: tuple[str, ...] = ()
    validated_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True, slots=True)
class MarketWorkerStatus:
    market: Market
    environment: BrokerEnvironment
    capabilities: frozenset[BrokerCapability]
    last_validated_at: datetime | None = None
    last_health_check_at: datetime | None = None
    last_reconciled_at: datetime | None = None