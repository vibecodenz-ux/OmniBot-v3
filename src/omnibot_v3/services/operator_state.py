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
    profile_id: str
    updated_at: datetime = field(default_factory=utc_now)
    strategy_id: str | None = None


@dataclass(frozen=True, slots=True)
class OperatorStateSnapshot:
    admin_password_hash: str | None = None
    trading_module_selections: dict[Market, StoredTradingModuleSelection] = field(default_factory=dict)
    active_trade_theses: dict[Market, dict[str, dict[str, object]]] = field(default_factory=dict)
    closed_trade_theses: dict[Market, dict[str, dict[str, object]]] = field(default_factory=dict)
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

    def get_active_trade_thesis(self, market: Market, symbol: str) -> dict[str, object] | None:
        thesis = self.store.load().active_trade_theses.get(market, {}).get(symbol.strip().upper())
        return dict(thesis) if isinstance(thesis, dict) else None

    def list_active_trade_theses(self, market: Market) -> dict[str, dict[str, object]]:
        theses = self.store.load().active_trade_theses.get(market, {})
        return {
            symbol: dict(thesis)
            for symbol, thesis in theses.items()
            if isinstance(thesis, dict)
        }

    def get_closed_trade_thesis(self, market: Market, trade_id: str) -> dict[str, object] | None:
        thesis = self.store.load().closed_trade_theses.get(market, {}).get(str(trade_id))
        return dict(thesis) if isinstance(thesis, dict) else None

    def find_closed_trade_thesis(
        self,
        market: Market,
        *,
        trade_id: str | None = None,
        symbol: str | None = None,
        opened_at: datetime | None = None,
        closed_at: datetime | None = None,
    ) -> dict[str, object] | None:
        if trade_id is not None:
            thesis = self.get_closed_trade_thesis(market, trade_id)
            if thesis is not None:
                return thesis

        normalized_symbol = symbol.strip().upper() if isinstance(symbol, str) and symbol.strip() else None
        if normalized_symbol is None:
            return None

        for thesis in self.store.load().closed_trade_theses.get(market, {}).values():
            if not isinstance(thesis, dict):
                continue
            if str(thesis.get("trade_symbol") or "").strip().upper() != normalized_symbol:
                continue
            if opened_at is not None and str(thesis.get("opened_at") or "") != opened_at.isoformat():
                continue
            if closed_at is not None and str(thesis.get("closed_at") or "") != closed_at.isoformat():
                continue
            return dict(thesis)
        return None

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
        profile_id: str,
        updated_at: datetime | None = None,
        now: datetime | None = None,
    ) -> None:
        snapshot = self.store.load()
        selections = dict(snapshot.trading_module_selections)
        selections[market] = StoredTradingModuleSelection(
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

    def upsert_active_trade_thesis(
        self,
        market: Market,
        symbol: str,
        thesis: dict[str, object],
        *,
        now: datetime | None = None,
    ) -> None:
        snapshot = self.store.load()
        theses = {
            stored_market: {
                stored_symbol: dict(stored_thesis)
                for stored_symbol, stored_thesis in stored_theses.items()
                if isinstance(stored_thesis, dict)
            }
            for stored_market, stored_theses in snapshot.active_trade_theses.items()
        }
        market_theses = dict(theses.get(market, {}))
        market_theses[symbol.strip().upper()] = _with_lifecycle_defaults(
            thesis,
            state=str(thesis.get("lifecycle_state") or "active"),
            reason=str(thesis.get("lifecycle_reason") or "entry-submitted"),
            transitioned_at=_coerce_transition_timestamp(thesis.get("last_transition_at"), fallback=now or utc_now()),
        )
        theses[market] = market_theses
        self.store.save(
            replace(
                snapshot,
                active_trade_theses=theses,
                updated_at=now or utc_now(),
            )
        )

    def transition_active_trade_thesis(
        self,
        market: Market,
        symbol: str,
        *,
        state: str,
        reason: str,
        transitioned_at: datetime | None = None,
        now: datetime | None = None,
    ) -> None:
        snapshot = self.store.load()
        theses = {
            stored_market: {
                stored_symbol: dict(stored_thesis)
                for stored_symbol, stored_thesis in stored_theses.items()
                if isinstance(stored_thesis, dict)
            }
            for stored_market, stored_theses in snapshot.active_trade_theses.items()
        }
        market_key = symbol.strip().upper()
        market_theses = dict(theses.get(market, {}))
        thesis = market_theses.get(market_key)
        if thesis is None:
            return
        market_theses[market_key] = _with_lifecycle_defaults(
            thesis,
            state=state,
            reason=reason,
            transitioned_at=transitioned_at or utc_now(),
        )
        theses[market] = market_theses
        self.store.save(
            replace(
                snapshot,
                active_trade_theses=theses,
                updated_at=now or utc_now(),
            )
        )

    def remove_active_trade_thesis(
        self,
        market: Market,
        symbol: str,
        *,
        now: datetime | None = None,
    ) -> None:
        snapshot = self.store.load()
        theses = {
            stored_market: {
                stored_symbol: dict(stored_thesis)
                for stored_symbol, stored_thesis in stored_theses.items()
                if isinstance(stored_thesis, dict)
            }
            for stored_market, stored_theses in snapshot.active_trade_theses.items()
        }
        market_theses = dict(theses.get(market, {}))
        market_theses.pop(symbol.strip().upper(), None)
        if market_theses:
            theses[market] = market_theses
        else:
            theses.pop(market, None)
        self.store.save(
            replace(
                snapshot,
                active_trade_theses=theses,
                updated_at=now or utc_now(),
            )
        )

    def archive_closed_trade_thesis(
        self,
        market: Market,
        trade_id: str,
        thesis: dict[str, object],
        *,
        trade_symbol: str | None = None,
        opened_at: datetime | None = None,
        closed_at: datetime | None = None,
        state: str | None = None,
        reason: str | None = None,
        transitioned_at: datetime | None = None,
        now: datetime | None = None,
    ) -> None:
        snapshot = self.store.load()
        closed_theses = {
            stored_market: {
                stored_trade_id: dict(stored_thesis)
                for stored_trade_id, stored_thesis in stored_theses.items()
                if isinstance(stored_thesis, dict)
            }
            for stored_market, stored_theses in snapshot.closed_trade_theses.items()
        }
        market_closed_theses = dict(closed_theses.get(market, {}))
        archived_thesis = _with_lifecycle_defaults(
            thesis,
            state=state or str(thesis.get("lifecycle_state") or "closed"),
            reason=reason or str(thesis.get("lifecycle_reason") or "position-closed"),
            transitioned_at=transitioned_at or _coerce_transition_timestamp(thesis.get("last_transition_at"), fallback=now or utc_now()),
        )
        if trade_symbol is not None:
            archived_thesis["trade_symbol"] = trade_symbol
        if opened_at is not None:
            archived_thesis["opened_at"] = opened_at.isoformat()
        if closed_at is not None:
            archived_thesis["closed_at"] = closed_at.isoformat()
        market_closed_theses[str(trade_id)] = archived_thesis
        closed_theses[market] = market_closed_theses
        self.store.save(
            replace(
                snapshot,
                closed_trade_theses=closed_theses,
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


def _with_lifecycle_defaults(
    thesis: dict[str, object],
    *,
    state: str,
    reason: str,
    transitioned_at: datetime,
) -> dict[str, object]:
    enriched = dict(thesis)
    enriched["lifecycle_state"] = state
    enriched["lifecycle_reason"] = reason
    enriched["last_transition_at"] = transitioned_at.isoformat()
    return enriched


def _coerce_transition_timestamp(value: object, *, fallback: datetime) -> datetime:
    if isinstance(value, datetime):
        return value
    if value is None:
        return fallback
    return datetime.fromisoformat(str(value))