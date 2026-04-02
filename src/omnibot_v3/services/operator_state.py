"""Persistence service for restart-stable operator-facing dashboard state."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Protocol

from omnibot_v3.domain.runtime import Market


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class StoredTradingModuleSelection:
    strategy_id: str
    profile_id: str
    updated_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True, slots=True)
class OperatorStateSnapshot:
    admin_password_hash: str | None = None
    trading_module_selections: dict[Market, StoredTradingModuleSelection] = field(default_factory=dict)
    closed_trade_history_cleared_before: datetime | None = None
    updated_at: datetime = field(default_factory=utc_now)


class OperatorStateStore(Protocol):
    def load(self) -> OperatorStateSnapshot:
        """Return the latest persisted operator state."""

    def save(self, snapshot: OperatorStateSnapshot) -> None:
        """Persist operator-facing dashboard state."""


@dataclass(slots=True)
class OperatorStateService:
    store: OperatorStateStore

    def get_admin_password_hash(self) -> str | None:
        return self.store.load().admin_password_hash

    def list_trading_module_selections(self) -> dict[Market, StoredTradingModuleSelection]:
        return dict(self.store.load().trading_module_selections)

    def get_closed_trade_history_cleared_before(self) -> datetime | None:
        return self.store.load().closed_trade_history_cleared_before

    def update_admin_password_hash(self, password_hash: str, *, now: datetime | None = None) -> None:
        snapshot = self.store.load()
        self.store.save(
            replace(
                snapshot,
                admin_password_hash=password_hash,
                updated_at=now or utc_now(),
            )
        )

    def update_trading_module_selection(
        self,
        market: Market,
        *,
        strategy_id: str,
        profile_id: str,
        updated_at: datetime | None = None,
        now: datetime | None = None,
    ) -> None:
        snapshot = self.store.load()
        selections = dict(snapshot.trading_module_selections)
        selections[market] = StoredTradingModuleSelection(
            strategy_id=strategy_id,
            profile_id=profile_id,
            updated_at=updated_at or utc_now(),
        )
        self.store.save(
            replace(
                snapshot,
                trading_module_selections=selections,
                updated_at=now or utc_now(),
            )
        )

    def clear_closed_trade_history(self, *, cleared_before: datetime | None = None, now: datetime | None = None) -> None:
        snapshot = self.store.load()
        self.store.save(
            replace(
                snapshot,
                closed_trade_history_cleared_before=cleared_before or utc_now(),
                updated_at=now or utc_now(),
            )
        )