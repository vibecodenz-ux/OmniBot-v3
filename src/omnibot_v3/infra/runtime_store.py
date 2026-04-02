"""In-memory and JSON-backed runtime persistence adapters."""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from tempfile import NamedTemporaryFile

from omnibot_v3.domain.broker import (
    NormalizedAccount,
    NormalizedFill,
    NormalizedOrder,
    NormalizedPosition,
    NormalizedTrade,
    OrderSide,
    OrderStatus,
    OrderType,
    PortfolioSnapshot,
    TimeInForce,
)
from omnibot_v3.domain.contracts import RuntimeEvent
from omnibot_v3.domain.runtime import Market, MarketRuntimeSnapshot
from omnibot_v3.services.runtime_store import (
    PortfolioSnapshotStore,
    RuntimeEventStore,
    RuntimeSnapshotStore,
)


@dataclass(slots=True)
class InMemoryRuntimeSnapshotStore(RuntimeSnapshotStore):
    snapshots: dict[Market, MarketRuntimeSnapshot] = field(default_factory=dict)

    def load_all(self) -> dict[Market, MarketRuntimeSnapshot]:
        return dict(self.snapshots)

    def save(self, snapshot: MarketRuntimeSnapshot) -> None:
        self.snapshots[snapshot.market] = snapshot


@dataclass(slots=True)
class InMemoryRuntimeEventStore(RuntimeEventStore):
    events: list[RuntimeEvent] = field(default_factory=list)

    def append(self, events: list[RuntimeEvent]) -> None:
        self.events.extend(events)

    def list_events(self) -> list[RuntimeEvent]:
        return list(self.events)


@dataclass(slots=True)
class InMemoryPortfolioSnapshotStore(PortfolioSnapshotStore):
    snapshots: dict[Market, PortfolioSnapshot] = field(default_factory=dict)

    def load_all(self) -> dict[Market, PortfolioSnapshot]:
        return dict(self.snapshots)

    def save(self, snapshot: PortfolioSnapshot) -> None:
        existing = self.snapshots.get(snapshot.market)
        self.snapshots[snapshot.market] = _merge_portfolio_snapshot(existing, snapshot)


@dataclass(frozen=True, slots=True)
class JsonFilePortfolioSnapshotStore(PortfolioSnapshotStore):
    path: Path

    def load_all(self) -> dict[Market, PortfolioSnapshot]:
        if not self.path.is_file():
            return {}

        payload = json.loads(self.path.read_text(encoding="utf-8"))
        raw_snapshots = payload.get("snapshots", [])
        if not isinstance(raw_snapshots, list):
            raise ValueError("Portfolio snapshot file has an invalid snapshots payload.")

        snapshots = [_snapshot_from_payload(item) for item in raw_snapshots]
        return {snapshot.market: snapshot for snapshot in snapshots}

    def save(self, snapshot: PortfolioSnapshot) -> None:
        snapshots = self.load_all()
        snapshots[snapshot.market] = _merge_portfolio_snapshot(snapshots.get(snapshot.market), snapshot)
        self._write(snapshots.values())

    def _write(self, snapshots: Iterable[PortfolioSnapshot]) -> None:
        normalized_snapshots = sorted(list(snapshots), key=lambda item: item.market.value)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"snapshots": [_snapshot_to_payload(snapshot) for snapshot in normalized_snapshots]}
        with NamedTemporaryFile("w", encoding="utf-8", dir=self.path.parent, delete=False) as handle:
            temp_path = Path(handle.name)
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.flush()
        temp_path.replace(self.path)


def _snapshot_from_payload(payload: object) -> PortfolioSnapshot:
    if not isinstance(payload, dict):
        raise ValueError("Portfolio snapshot entry must be an object.")
    return PortfolioSnapshot(
        market=Market(str(payload["market"])),
        account=_account_from_payload(payload.get("account")),
        positions=tuple(_position_from_payload(item) for item in _list_payload(payload.get("positions"))),
        open_orders=tuple(_order_from_payload(item) for item in _list_payload(payload.get("open_orders"))),
        fills=tuple(_fill_from_payload(item) for item in _list_payload(payload.get("fills"))),
        closed_trades=tuple(_trade_from_payload(item) for item in _list_payload(payload.get("closed_trades"))),
        as_of=_parse_datetime(payload.get("as_of")),
    )


def _snapshot_to_payload(snapshot: PortfolioSnapshot) -> dict[str, object]:
    return {
        "market": snapshot.market.value,
        "account": _account_to_payload(snapshot.account),
        "positions": [_position_to_payload(item) for item in snapshot.positions],
        "open_orders": [_order_to_payload(item) for item in snapshot.open_orders],
        "fills": [_fill_to_payload(item) for item in snapshot.fills],
        "closed_trades": [_trade_to_payload(item) for item in snapshot.closed_trades],
        "as_of": snapshot.as_of.isoformat(),
    }


def _merge_portfolio_snapshot(
    existing: PortfolioSnapshot | None,
    incoming: PortfolioSnapshot,
) -> PortfolioSnapshot:
    if existing is None:
        return incoming
    merged_trades = _merge_closed_trades(existing.closed_trades, incoming.closed_trades)
    if merged_trades == incoming.closed_trades:
        return incoming
    return PortfolioSnapshot(
        market=incoming.market,
        account=incoming.account,
        positions=incoming.positions,
        open_orders=incoming.open_orders,
        fills=incoming.fills,
        closed_trades=merged_trades,
        as_of=incoming.as_of,
    )


def _merge_closed_trades(
    existing: tuple[NormalizedTrade, ...],
    incoming: tuple[NormalizedTrade, ...],
) -> tuple[NormalizedTrade, ...]:
    if not existing:
        return incoming
    if not incoming:
        return existing
    merged: list[NormalizedTrade] = []
    for trade in sorted((*existing, *incoming), key=lambda item: item.closed_at, reverse=True):
        match_index = next(
            (index for index, candidate in enumerate(merged) if _trade_records_match(candidate, trade)),
            None,
        )
        if match_index is None:
            merged.append(trade)
            continue
        merged[match_index] = _prefer_trade_record(merged[match_index], trade)
    return tuple(sorted(merged, key=lambda trade: trade.closed_at, reverse=True))


def _trade_records_match(left: NormalizedTrade, right: NormalizedTrade) -> bool:
    if left.trade_id == right.trade_id:
        return True
    return (
        left.market == right.market
        and left.symbol == right.symbol
        and left.side == right.side
        and left.quantity == right.quantity
        and left.exit_price == right.exit_price
        and abs(left.closed_at - right.closed_at) <= timedelta(seconds=90)
    )


def _prefer_trade_record(left: NormalizedTrade, right: NormalizedTrade) -> NormalizedTrade:
    if right.fees != left.fees:
        if right.fees != Decimal("0") and left.fees == Decimal("0"):
            return right
        if left.fees != Decimal("0") and right.fees == Decimal("0"):
            return left
    if right.opened_at != left.opened_at:
        return right if right.opened_at < left.opened_at else left
    return right if right.closed_at >= left.closed_at else left


def _account_from_payload(payload: object) -> NormalizedAccount:
    if not isinstance(payload, dict):
        raise ValueError("Portfolio snapshot account payload is invalid.")
    return NormalizedAccount(
        account_id=str(payload["account_id"]),
        currency=str(payload.get("currency") or "USD"),
        equity=Decimal(str(payload.get("equity") or "0")),
        buying_power=Decimal(str(payload.get("buying_power") or "0")),
        cash=Decimal(str(payload.get("cash") or "0")),
        updated_at=_parse_datetime(payload.get("updated_at")),
    )


def _account_to_payload(account: NormalizedAccount) -> dict[str, object]:
    return {
        "account_id": account.account_id,
        "currency": account.currency,
        "equity": str(account.equity),
        "buying_power": str(account.buying_power),
        "cash": str(account.cash),
        "updated_at": account.updated_at.isoformat(),
    }


def _position_from_payload(payload: object) -> NormalizedPosition:
    if not isinstance(payload, dict):
        raise ValueError("Portfolio snapshot position payload is invalid.")
    return NormalizedPosition(
        symbol=str(payload["symbol"]),
        quantity=Decimal(str(payload.get("quantity") or "0")),
        average_price=Decimal(str(payload.get("average_price") or "0")),
        market_price=Decimal(str(payload.get("market_price") or "0")),
        updated_at=_parse_datetime(payload.get("updated_at")),
    )


def _position_to_payload(position: NormalizedPosition) -> dict[str, object]:
    return {
        "symbol": position.symbol,
        "quantity": str(position.quantity),
        "average_price": str(position.average_price),
        "market_price": str(position.market_price),
        "updated_at": position.updated_at.isoformat(),
    }


def _order_from_payload(payload: object) -> NormalizedOrder:
    if not isinstance(payload, dict):
        raise ValueError("Portfolio snapshot order payload is invalid.")
    return NormalizedOrder(
        order_id=str(payload["order_id"]),
        client_order_id=str(payload["client_order_id"]),
        symbol=str(payload["symbol"]),
        side=OrderSide(str(payload["side"])),
        quantity=Decimal(str(payload.get("quantity") or "0")),
        filled_quantity=Decimal(str(payload.get("filled_quantity") or "0")),
        order_type=OrderType(str(payload["order_type"])),
        status=OrderStatus(str(payload["status"])),
        time_in_force=TimeInForce(str(payload.get("time_in_force") or TimeInForce.DAY.value)),
        limit_price=_optional_decimal(payload.get("limit_price")),
        average_fill_price=_optional_decimal(payload.get("average_fill_price")),
        submitted_at=_parse_datetime(payload.get("submitted_at")),
    )


def _order_to_payload(order: NormalizedOrder) -> dict[str, object]:
    return {
        "order_id": order.order_id,
        "client_order_id": order.client_order_id,
        "symbol": order.symbol,
        "side": order.side.value,
        "quantity": str(order.quantity),
        "filled_quantity": str(order.filled_quantity),
        "order_type": order.order_type.value,
        "status": order.status.value,
        "time_in_force": order.time_in_force.value,
        "limit_price": None if order.limit_price is None else str(order.limit_price),
        "average_fill_price": None if order.average_fill_price is None else str(order.average_fill_price),
        "submitted_at": order.submitted_at.isoformat(),
    }


def _fill_from_payload(payload: object) -> NormalizedFill:
    if not isinstance(payload, dict):
        raise ValueError("Portfolio snapshot fill payload is invalid.")
    return NormalizedFill(
        fill_id=str(payload["fill_id"]),
        order_id=str(payload["order_id"]),
        client_order_id=str(payload["client_order_id"]),
        symbol=str(payload["symbol"]),
        side=OrderSide(str(payload["side"])),
        quantity=Decimal(str(payload.get("quantity") or "0")),
        price=Decimal(str(payload.get("price") or "0")),
        commission=Decimal(str(payload.get("commission") or "0")),
        executed_at=_parse_datetime(payload.get("executed_at")),
    )


def _fill_to_payload(fill: NormalizedFill) -> dict[str, object]:
    return {
        "fill_id": fill.fill_id,
        "order_id": fill.order_id,
        "client_order_id": fill.client_order_id,
        "symbol": fill.symbol,
        "side": fill.side.value,
        "quantity": str(fill.quantity),
        "price": str(fill.price),
        "commission": str(fill.commission),
        "executed_at": fill.executed_at.isoformat(),
    }


def _trade_from_payload(payload: object) -> NormalizedTrade:
    if not isinstance(payload, dict):
        raise ValueError("Portfolio snapshot trade payload is invalid.")
    return NormalizedTrade(
        trade_id=str(payload["trade_id"]),
        market=Market(str(payload["market"])),
        symbol=str(payload["symbol"]),
        side=OrderSide(str(payload["side"])),
        quantity=Decimal(str(payload.get("quantity") or "0")),
        entry_price=Decimal(str(payload.get("entry_price") or "0")),
        exit_price=Decimal(str(payload.get("exit_price") or "0")),
        opened_at=_parse_datetime(payload.get("opened_at")),
        closed_at=_parse_datetime(payload.get("closed_at")),
        fees=Decimal(str(payload.get("fees") or "0")),
    )


def _trade_to_payload(trade: NormalizedTrade) -> dict[str, object]:
    return {
        "trade_id": trade.trade_id,
        "market": trade.market.value,
        "symbol": trade.symbol,
        "side": trade.side.value,
        "quantity": str(trade.quantity),
        "entry_price": str(trade.entry_price),
        "exit_price": str(trade.exit_price),
        "opened_at": trade.opened_at.isoformat(),
        "closed_at": trade.closed_at.isoformat(),
        "fees": str(trade.fees),
    }


def _list_payload(value: object) -> list[object]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("Portfolio snapshot list payload is invalid.")
    return value


def _parse_datetime(value: object) -> datetime:
    if value is None:
        raise ValueError("Portfolio snapshot timestamp is required.")
    return datetime.fromisoformat(str(value))


def _optional_decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))