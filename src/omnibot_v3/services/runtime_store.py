"""Persistence ports for runtime and portfolio snapshots plus audit events."""

from __future__ import annotations

from typing import Protocol

from omnibot_v3.domain.broker import PortfolioSnapshot
from omnibot_v3.domain.contracts import RuntimeEvent
from omnibot_v3.domain.runtime import Market, MarketRuntimeSnapshot


class RuntimeSnapshotStore(Protocol):
    def load_all(self) -> dict[Market, MarketRuntimeSnapshot]:
        """Load the latest snapshot for each market."""

    def save(self, snapshot: MarketRuntimeSnapshot) -> None:
        """Persist the latest snapshot for a market."""


class RuntimeEventStore(Protocol):
    def append(self, events: list[RuntimeEvent]) -> None:
        """Persist newly emitted runtime events."""

    def list_events(self) -> list[RuntimeEvent]:
        """Return persisted runtime events in append order."""


class PortfolioSnapshotStore(Protocol):
    def load_all(self) -> dict[Market, PortfolioSnapshot]:
        """Load the latest portfolio snapshot for each market."""

    def save(self, snapshot: PortfolioSnapshot) -> None:
        """Persist the latest portfolio snapshot for a market."""