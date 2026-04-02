"""Application command and event contracts for OmniBot v3."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from omnibot_v3.domain.runtime import Market, RuntimeState


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True, kw_only=True)
class RuntimeCommand:
    market: Market
    requested_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True, slots=True, kw_only=True)
class ConnectMarket(RuntimeCommand):
    pass


@dataclass(frozen=True, slots=True, kw_only=True)
class DisconnectMarket(RuntimeCommand):
    pass


@dataclass(frozen=True, slots=True, kw_only=True)
class ArmMarket(RuntimeCommand):
    pass


@dataclass(frozen=True, slots=True, kw_only=True)
class DisarmMarket(RuntimeCommand):
    pass


@dataclass(frozen=True, slots=True, kw_only=True)
class StartMarket(RuntimeCommand):
    pass


@dataclass(frozen=True, slots=True, kw_only=True)
class StopMarket(RuntimeCommand):
    pass


@dataclass(frozen=True, slots=True, kw_only=True)
class ReconcileMarket(RuntimeCommand):
    pass


@dataclass(frozen=True, slots=True, kw_only=True)
class CompleteMarketReconciliation(RuntimeCommand):
    pass


@dataclass(frozen=True, slots=True, kw_only=True)
class EngageKillSwitch(RuntimeCommand):
    reason: str = "market kill switch engaged"


@dataclass(frozen=True, slots=True, kw_only=True)
class ReleaseKillSwitch(RuntimeCommand):
    reason: str = "market kill switch released"


@dataclass(frozen=True, slots=True, kw_only=True)
class MarkMarketError(RuntimeCommand):
    message: str


@dataclass(frozen=True, slots=True, kw_only=True)
class EmergencyDisarmAll:
    requested_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True, slots=True, kw_only=True)
class RecoverRuntime:
    requested_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True, slots=True, kw_only=True)
class GracefulShutdownAll:
    requested_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True, slots=True, kw_only=True)
class RuntimeEvent:
    market: Market
    occurred_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True, slots=True, kw_only=True)
class MarketStateTransitioned(RuntimeEvent):
    previous_state: RuntimeState
    new_state: RuntimeState
    reason: str


@dataclass(frozen=True, slots=True, kw_only=True)
class MarketReconciliationRequested(RuntimeEvent):
    reason: str = "manual"


@dataclass(frozen=True, slots=True, kw_only=True)
class MarketReconciliationCompleted(RuntimeEvent):
    reason: str = "completed"


@dataclass(frozen=True, slots=True, kw_only=True)
class MarketKillSwitchEngaged(RuntimeEvent):
    previous_state: RuntimeState
    new_state: RuntimeState
    reason: str


@dataclass(frozen=True, slots=True, kw_only=True)
class MarketKillSwitchReleased(RuntimeEvent):
    reason: str = "market kill switch released"