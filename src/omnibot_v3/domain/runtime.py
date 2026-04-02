"""Core runtime domain types for OmniBot v3."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum


def utc_now() -> datetime:
    return datetime.now(UTC)


class Market(StrEnum):
    STOCKS = "stocks"
    CRYPTO = "crypto"
    FOREX = "forex"


class RuntimeState(StrEnum):
    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    IDLE = "IDLE"
    ARMED = "ARMED"
    RUNNING = "RUNNING"
    STOPPING = "STOPPING"
    ERROR = "ERROR"


@dataclass(frozen=True, slots=True)
class MarketRuntimeSnapshot:
    market: Market
    state: RuntimeState = RuntimeState.DISCONNECTED
    kill_switch_engaged: bool = False
    reconciliation_pending: bool = False
    last_reconciled_at: datetime | None = None
    last_error: str | None = None
    updated_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True, slots=True)
class MarketStateChange:
    market: Market
    previous_state: RuntimeState
    new_state: RuntimeState
    reason: str
    changed_at: datetime = field(default_factory=utc_now)


class InvalidStateTransitionError(ValueError):
    """Raised when a command would violate the runtime state machine."""