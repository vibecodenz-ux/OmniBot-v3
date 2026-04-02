"""PostgreSQL-backed runtime store foundation built on an abstract SQL executor."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Protocol

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
from omnibot_v3.domain.contracts import (
    MarketKillSwitchEngaged,
    MarketKillSwitchReleased,
    MarketReconciliationCompleted,
    MarketReconciliationRequested,
    MarketStateTransitioned,
    RuntimeEvent,
)
from omnibot_v3.domain.data_lifecycle import RuntimeEventRetentionPolicy
from omnibot_v3.domain.runtime import Market, MarketRuntimeSnapshot, RuntimeState
from omnibot_v3.services.runtime_store import (
    PortfolioSnapshotStore,
    RuntimeEventStore,
    RuntimeSnapshotStore,
)


class SqlExecutor(Protocol):
    def execute(self, query: str, params: dict[str, Any] | None = None) -> None:
        """Execute a write statement."""

    def fetch_all(self, query: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Execute a read statement and return rows as dictionaries."""


@dataclass(frozen=True, slots=True)
class PostgresRuntimeStoreConfig:
    dsn: str
    schema_name: str = "omnibot"
    snapshot_table: str = "market_runtime_snapshots"
    portfolio_snapshot_table: str = "portfolio_snapshots"
    event_table: str = "runtime_events"


def build_runtime_store_schema_sql(config: PostgresRuntimeStoreConfig) -> str:
    return f"""
CREATE SCHEMA IF NOT EXISTS {config.schema_name};

CREATE TABLE IF NOT EXISTS {config.schema_name}.{config.snapshot_table} (
    market TEXT PRIMARY KEY,
    state TEXT NOT NULL,
    kill_switch_engaged BOOLEAN NOT NULL,
    reconciliation_pending BOOLEAN NOT NULL,
    last_reconciled_at TIMESTAMPTZ NULL,
    last_error TEXT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS {config.schema_name}.{config.portfolio_snapshot_table} (
    market TEXT PRIMARY KEY,
    payload JSONB NOT NULL,
    as_of TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS {config.schema_name}.{config.event_table} (
    event_id BIGSERIAL PRIMARY KEY,
    market TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload JSONB NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_{config.event_table}_market_occurred_at
ON {config.schema_name}.{config.event_table} (market, occurred_at);
""".strip()


def build_runtime_event_archive_schema_sql(
    config: PostgresRuntimeStoreConfig,
    policy: RuntimeEventRetentionPolicy,
) -> str:
    return f"""
CREATE SCHEMA IF NOT EXISTS {policy.archive_schema_name};

CREATE TABLE IF NOT EXISTS {policy.archive_schema_name}.{policy.archive_event_table} (
    event_id BIGINT PRIMARY KEY,
    market TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload JSONB NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL,
    archived_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_{policy.archive_event_table}_market_occurred_at
ON {policy.archive_schema_name}.{policy.archive_event_table} (market, occurred_at);
""".strip()


def serialize_snapshot(snapshot: MarketRuntimeSnapshot) -> dict[str, Any]:
    return {
        "market": snapshot.market.value,
        "state": snapshot.state.value,
        "kill_switch_engaged": snapshot.kill_switch_engaged,
        "reconciliation_pending": snapshot.reconciliation_pending,
        "last_reconciled_at": snapshot.last_reconciled_at.isoformat()
        if snapshot.last_reconciled_at is not None
        else None,
        "last_error": snapshot.last_error,
        "updated_at": snapshot.updated_at.isoformat(),
    }


def deserialize_snapshot(row: dict[str, Any]) -> MarketRuntimeSnapshot:
    last_reconciled_at = row.get("last_reconciled_at")
    return MarketRuntimeSnapshot(
        market=Market(str(row["market"])),
        state=RuntimeState(str(row["state"])),
        kill_switch_engaged=bool(row["kill_switch_engaged"]),
        reconciliation_pending=bool(row["reconciliation_pending"]),
        last_reconciled_at=datetime.fromisoformat(last_reconciled_at)
        if last_reconciled_at is not None
        else None,
        last_error=row.get("last_error"),
        updated_at=datetime.fromisoformat(str(row["updated_at"])),
    )


def serialize_portfolio_snapshot(snapshot: PortfolioSnapshot) -> dict[str, Any]:
    return {
        "market": snapshot.market.value,
        "payload": {
            "market": snapshot.market.value,
            "account": {
                "account_id": snapshot.account.account_id,
                "currency": snapshot.account.currency,
                "equity": str(snapshot.account.equity),
                "buying_power": str(snapshot.account.buying_power),
                "cash": str(snapshot.account.cash),
                "updated_at": snapshot.account.updated_at.isoformat(),
            },
            "positions": [
                {
                    "symbol": position.symbol,
                    "quantity": str(position.quantity),
                    "average_price": str(position.average_price),
                    "market_price": str(position.market_price),
                    "updated_at": position.updated_at.isoformat(),
                }
                for position in snapshot.positions
            ],
            "open_orders": [
                {
                    "order_id": order.order_id,
                    "client_order_id": order.client_order_id,
                    "symbol": order.symbol,
                    "side": order.side.value,
                    "quantity": str(order.quantity),
                    "filled_quantity": str(order.filled_quantity),
                    "order_type": order.order_type.value,
                    "status": order.status.value,
                    "time_in_force": order.time_in_force.value,
                    "limit_price": str(order.limit_price) if order.limit_price is not None else None,
                    "average_fill_price": str(order.average_fill_price)
                    if order.average_fill_price is not None
                    else None,
                    "submitted_at": order.submitted_at.isoformat(),
                }
                for order in snapshot.open_orders
            ],
            "fills": [
                {
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
                for fill in snapshot.fills
            ],
            "closed_trades": [
                {
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
                for trade in snapshot.closed_trades
            ],
            "as_of": snapshot.as_of.isoformat(),
        },
        "as_of": snapshot.as_of.isoformat(),
    }


def deserialize_portfolio_snapshot(row: dict[str, Any]) -> PortfolioSnapshot:
    payload = dict(row["payload"])
    account_payload = dict(payload["account"])
    return PortfolioSnapshot(
        market=Market(str(payload["market"])),
        account=NormalizedAccount(
            account_id=str(account_payload["account_id"]),
            currency=str(account_payload["currency"]),
            equity=Decimal(str(account_payload["equity"])),
            buying_power=Decimal(str(account_payload["buying_power"])),
            cash=Decimal(str(account_payload["cash"])),
            updated_at=datetime.fromisoformat(str(account_payload["updated_at"])),
        ),
        positions=tuple(
            NormalizedPosition(
                symbol=str(position["symbol"]),
                quantity=Decimal(str(position["quantity"])),
                average_price=Decimal(str(position["average_price"])),
                market_price=Decimal(str(position["market_price"])),
                updated_at=datetime.fromisoformat(str(position["updated_at"])),
            )
            for position in payload["positions"]
        ),
        open_orders=tuple(
            NormalizedOrder(
                order_id=str(order["order_id"]),
                client_order_id=str(order["client_order_id"]),
                symbol=str(order["symbol"]),
                side=OrderSide(str(order["side"])),
                quantity=Decimal(str(order["quantity"])),
                filled_quantity=Decimal(str(order["filled_quantity"])),
                order_type=OrderType(str(order["order_type"])),
                status=OrderStatus(str(order["status"])),
                time_in_force=TimeInForce(str(order["time_in_force"])),
                limit_price=Decimal(str(order["limit_price"])) if order["limit_price"] is not None else None,
                average_fill_price=Decimal(str(order["average_fill_price"]))
                if order["average_fill_price"] is not None
                else None,
                submitted_at=datetime.fromisoformat(str(order["submitted_at"])),
            )
            for order in payload["open_orders"]
        ),
        fills=tuple(
            NormalizedFill(
                fill_id=str(fill["fill_id"]),
                order_id=str(fill["order_id"]),
                client_order_id=str(fill["client_order_id"]),
                symbol=str(fill["symbol"]),
                side=OrderSide(str(fill["side"])),
                quantity=Decimal(str(fill["quantity"])),
                price=Decimal(str(fill["price"])),
                commission=Decimal(str(fill["commission"])),
                executed_at=datetime.fromisoformat(str(fill["executed_at"])),
            )
            for fill in payload["fills"]
        ),
        closed_trades=tuple(
            NormalizedTrade(
                trade_id=str(trade["trade_id"]),
                market=Market(str(trade["market"])),
                symbol=str(trade["symbol"]),
                side=OrderSide(str(trade["side"])),
                quantity=Decimal(str(trade["quantity"])),
                entry_price=Decimal(str(trade["entry_price"])),
                exit_price=Decimal(str(trade["exit_price"])),
                opened_at=datetime.fromisoformat(str(trade["opened_at"])),
                closed_at=datetime.fromisoformat(str(trade["closed_at"])),
                fees=Decimal(str(trade["fees"])),
            )
            for trade in payload["closed_trades"]
        ),
        as_of=datetime.fromisoformat(str(payload["as_of"])),
    )


def serialize_event(event: RuntimeEvent) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "market": event.market.value,
        "occurred_at": event.occurred_at.isoformat(),
    }
    if isinstance(event, MarketStateTransitioned):
        payload |= {
            "previous_state": event.previous_state.value,
            "new_state": event.new_state.value,
            "reason": event.reason,
        }
    elif isinstance(event, MarketReconciliationRequested):
        payload |= {"reason": event.reason}
    elif isinstance(event, MarketReconciliationCompleted):
        payload |= {"reason": event.reason}
    elif isinstance(event, MarketKillSwitchEngaged):
        payload |= {
            "previous_state": event.previous_state.value,
            "new_state": event.new_state.value,
            "reason": event.reason,
        }
    elif isinstance(event, MarketKillSwitchReleased):
        payload |= {"reason": event.reason}
    else:
        raise ValueError(f"Unsupported runtime event type: {type(event).__name__}")

    return {"event_type": type(event).__name__, "payload": payload}


def deserialize_event(row: dict[str, Any]) -> RuntimeEvent:
    event_type = str(row["event_type"])
    payload = dict(row["payload"])
    market = Market(str(payload["market"]))
    occurred_at = datetime.fromisoformat(str(payload["occurred_at"]))

    event_types: dict[str, type[RuntimeEvent]] = {
        "MarketStateTransitioned": MarketStateTransitioned,
        "MarketReconciliationRequested": MarketReconciliationRequested,
        "MarketReconciliationCompleted": MarketReconciliationCompleted,
        "MarketKillSwitchEngaged": MarketKillSwitchEngaged,
        "MarketKillSwitchReleased": MarketKillSwitchReleased,
    }
    event_class = event_types.get(event_type)
    if event_class is None:
        raise ValueError(f"Unsupported runtime event type: {event_type}")

    if event_class is MarketStateTransitioned:
        return MarketStateTransitioned(
            market=market,
            occurred_at=occurred_at,
            previous_state=RuntimeState(str(payload["previous_state"])),
            new_state=RuntimeState(str(payload["new_state"])),
            reason=str(payload["reason"]),
        )
    if event_class is MarketReconciliationRequested:
        return MarketReconciliationRequested(
            market=market,
            occurred_at=occurred_at,
            reason=str(payload["reason"]),
        )
    if event_class is MarketReconciliationCompleted:
        return MarketReconciliationCompleted(
            market=market,
            occurred_at=occurred_at,
            reason=str(payload["reason"]),
        )
    if event_class is MarketKillSwitchEngaged:
        return MarketKillSwitchEngaged(
            market=market,
            occurred_at=occurred_at,
            previous_state=RuntimeState(str(payload["previous_state"])),
            new_state=RuntimeState(str(payload["new_state"])),
            reason=str(payload["reason"]),
        )

    return MarketKillSwitchReleased(
        market=market,
        occurred_at=occurred_at,
        reason=str(payload["reason"]),
    )


@dataclass(slots=True)
class PostgresRuntimeSnapshotStore(RuntimeSnapshotStore):
    config: PostgresRuntimeStoreConfig
    executor: SqlExecutor

    def create_schema(self) -> None:
        self.executor.execute(build_runtime_store_schema_sql(self.config))

    def load_all(self) -> dict[Market, MarketRuntimeSnapshot]:
        rows = self.executor.fetch_all(
            f"SELECT market, state, kill_switch_engaged, reconciliation_pending, "
            f"last_reconciled_at, last_error, updated_at "
            f"FROM {self.config.schema_name}.{self.config.snapshot_table}"
        )
        return {snapshot.market: snapshot for snapshot in map(deserialize_snapshot, rows)}

    def save(self, snapshot: MarketRuntimeSnapshot) -> None:
        self.executor.execute(
            (
                f"INSERT INTO {self.config.schema_name}.{self.config.snapshot_table} "
                f"(market, state, kill_switch_engaged, reconciliation_pending, last_reconciled_at, last_error, updated_at) "
                f"VALUES (%(market)s, %(state)s, %(kill_switch_engaged)s, %(reconciliation_pending)s, %(last_reconciled_at)s, %(last_error)s, %(updated_at)s) "
                f"ON CONFLICT (market) DO UPDATE SET "
                f"state = EXCLUDED.state, "
                f"kill_switch_engaged = EXCLUDED.kill_switch_engaged, "
                f"reconciliation_pending = EXCLUDED.reconciliation_pending, "
                f"last_reconciled_at = EXCLUDED.last_reconciled_at, "
                f"last_error = EXCLUDED.last_error, "
                f"updated_at = EXCLUDED.updated_at"
            ),
            serialize_snapshot(snapshot),
        )


@dataclass(slots=True)
class PostgresRuntimeEventStore(RuntimeEventStore):
    config: PostgresRuntimeStoreConfig
    executor: SqlExecutor

    def create_schema(self) -> None:
        self.executor.execute(build_runtime_store_schema_sql(self.config))

    def create_archive_schema(self, policy: RuntimeEventRetentionPolicy) -> None:
        self.executor.execute(build_runtime_event_archive_schema_sql(self.config, policy))

    def append(self, events: list[RuntimeEvent]) -> None:
        for event in events:
            serialized = serialize_event(event)
            payload = serialized["payload"]
            self.executor.execute(
                (
                    f"INSERT INTO {self.config.schema_name}.{self.config.event_table} "
                    f"(market, event_type, payload, occurred_at) "
                    f"VALUES (%(market)s, %(event_type)s, %(payload)s, %(occurred_at)s)"
                ),
                {
                    "market": payload["market"],
                    "event_type": serialized["event_type"],
                    "payload": payload,
                    "occurred_at": payload["occurred_at"],
                },
            )

    def list_events(self) -> list[RuntimeEvent]:
        rows = self.executor.fetch_all(
            f"SELECT event_type, payload "
            f"FROM {self.config.schema_name}.{self.config.event_table} "
            f"ORDER BY occurred_at, event_id"
        )
        return [deserialize_event(row) for row in rows]

    def archive_expired(self, now: datetime, policy: RuntimeEventRetentionPolicy) -> None:
        cutoff = (now - timedelta(days=policy.retention_days)).isoformat()
        self.executor.execute(
            (
                f"INSERT INTO {policy.archive_schema_name}.{policy.archive_event_table} "
                f"(event_id, market, event_type, payload, occurred_at) "
                f"SELECT event_id, market, event_type, payload, occurred_at "
                f"FROM {self.config.schema_name}.{self.config.event_table} "
                f"WHERE occurred_at < %(cutoff)s "
                f"ON CONFLICT (event_id) DO NOTHING"
            ),
            {"cutoff": cutoff},
        )
        self.executor.execute(
            (
                f"DELETE FROM {self.config.schema_name}.{self.config.event_table} "
                f"WHERE occurred_at < %(cutoff)s"
            ),
            {"cutoff": cutoff},
        )


@dataclass(slots=True)
class PostgresPortfolioSnapshotStore(PortfolioSnapshotStore):
    config: PostgresRuntimeStoreConfig
    executor: SqlExecutor

    def create_schema(self) -> None:
        self.executor.execute(build_runtime_store_schema_sql(self.config))

    def load_all(self) -> dict[Market, PortfolioSnapshot]:
        rows = self.executor.fetch_all(
            f"SELECT market, payload, as_of "
            f"FROM {self.config.schema_name}.{self.config.portfolio_snapshot_table}"
        )
        return {
            snapshot.market: snapshot for snapshot in map(deserialize_portfolio_snapshot, rows)
        }

    def save(self, snapshot: PortfolioSnapshot) -> None:
        self.executor.execute(
            (
                f"INSERT INTO {self.config.schema_name}.{self.config.portfolio_snapshot_table} "
                f"(market, payload, as_of) "
                f"VALUES (%(market)s, %(payload)s, %(as_of)s) "
                f"ON CONFLICT (market) DO UPDATE SET "
                f"payload = EXCLUDED.payload, "
                f"as_of = EXCLUDED.as_of"
            ),
            serialize_portfolio_snapshot(snapshot),
        )
