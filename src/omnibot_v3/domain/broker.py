"""Normalized broker-facing domain models and enums."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum

from omnibot_v3.domain.runtime import Market


def utc_now() -> datetime:
    return datetime.now(UTC)


class BrokerEnvironment(StrEnum):
    SANDBOX = "sandbox"
    LIVE = "live"


class BrokerCapability(StrEnum):
    SUBMIT_ORDER = "submit_order"
    CANCEL_ORDER = "cancel_order"
    QUERY_ORDER = "query_order"
    RECONCILE = "reconcile"
    HEALTH_CHECK = "health_check"
    HISTORICAL_BARS = "historical_bars"
    TRADE_HISTORY = "trade_history"


class BrokerHealthStatus(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


class BarTimeframe(StrEnum):
    ONE_MINUTE = "1m"
    FIVE_MINUTES = "5m"
    FIFTEEN_MINUTES = "15m"
    ONE_HOUR = "1h"
    ONE_DAY = "1d"


class OrderSide(StrEnum):
    BUY = "buy"
    SELL = "sell"


class OrderType(StrEnum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"


class TimeInForce(StrEnum):
    DAY = "day"
    GTC = "gtc"
    IOC = "ioc"


class OrderStatus(StrEnum):
    ACCEPTED = "accepted"
    OPEN = "open"
    FILLED = "filled"
    CANCELED = "canceled"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class BrokerSafetyPolicy:
    require_market_arming: bool = True
    allow_live_execution: bool = False
    supports_kill_switch: bool = True


@dataclass(frozen=True, slots=True)
class BrokerReconciliationPolicy:
    default_timeout_seconds: int = 30
    hard_timeout_seconds: int = 120


@dataclass(frozen=True, slots=True)
class BrokerMetadata:
    adapter_name: str
    market: Market
    environment: BrokerEnvironment
    capabilities: frozenset[BrokerCapability]
    safety_policy: BrokerSafetyPolicy
    reconciliation_policy: BrokerReconciliationPolicy


@dataclass(frozen=True, slots=True)
class BrokerHealth:
    status: BrokerHealthStatus
    message: str
    checked_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True, slots=True)
class HistoricalBar:
    market: Market
    symbol: str
    timeframe: BarTimeframe
    open_price: Decimal
    high_price: Decimal
    low_price: Decimal
    close_price: Decimal
    volume: Decimal = Decimal("0")
    opened_at: datetime = field(default_factory=utc_now)
    closed_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True, slots=True)
class NormalizedAccount:
    account_id: str
    currency: str
    equity: Decimal
    buying_power: Decimal
    cash: Decimal
    updated_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True, slots=True)
class NormalizedPosition:
    symbol: str
    quantity: Decimal
    average_price: Decimal
    market_price: Decimal
    updated_at: datetime = field(default_factory=utc_now)

    @property
    def cost_basis(self) -> Decimal:
        return self.quantity * self.average_price

    @property
    def market_value(self) -> Decimal:
        return self.quantity * self.market_price

    @property
    def unrealized_pnl(self) -> Decimal:
        return self.market_value - self.cost_basis


@dataclass(frozen=True, slots=True)
class OrderRequest:
    client_order_id: str
    symbol: str
    side: OrderSide
    quantity: Decimal
    order_type: OrderType
    time_in_force: TimeInForce = TimeInForce.DAY
    limit_price: Decimal | None = None


@dataclass(frozen=True, slots=True)
class NormalizedOrder:
    order_id: str
    client_order_id: str
    symbol: str
    side: OrderSide
    quantity: Decimal
    filled_quantity: Decimal
    order_type: OrderType
    status: OrderStatus
    time_in_force: TimeInForce
    limit_price: Decimal | None = None
    average_fill_price: Decimal | None = None
    submitted_at: datetime = field(default_factory=utc_now)

    @property
    def remaining_quantity(self) -> Decimal:
        return max(Decimal("0"), self.quantity - self.filled_quantity)

    @property
    def is_terminal(self) -> bool:
        return self.status in {OrderStatus.FILLED, OrderStatus.CANCELED, OrderStatus.REJECTED}


@dataclass(frozen=True, slots=True)
class NormalizedFill:
    fill_id: str
    order_id: str
    client_order_id: str
    symbol: str
    side: OrderSide
    quantity: Decimal
    price: Decimal
    commission: Decimal = Decimal("0")
    executed_at: datetime = field(default_factory=utc_now)

    @property
    def notional(self) -> Decimal:
        return self.quantity * self.price

    @property
    def signed_quantity(self) -> Decimal:
        if self.side == OrderSide.BUY:
            return self.quantity
        return -self.quantity


@dataclass(frozen=True, slots=True)
class NormalizedTrade:
    trade_id: str
    market: Market
    symbol: str
    side: OrderSide
    quantity: Decimal
    entry_price: Decimal
    exit_price: Decimal
    opened_at: datetime
    closed_at: datetime
    fees: Decimal = Decimal("0")

    @property
    def entry_notional(self) -> Decimal:
        return self.quantity * self.entry_price

    @property
    def exit_notional(self) -> Decimal:
        return self.quantity * self.exit_price

    @property
    def realized_pnl(self) -> Decimal:
        if self.side == OrderSide.BUY:
            gross_pnl = (self.exit_price - self.entry_price) * self.quantity
        else:
            gross_pnl = (self.entry_price - self.exit_price) * self.quantity
        return gross_pnl - self.fees


@dataclass(frozen=True, slots=True)
class PortfolioSnapshot:
    market: Market
    account: NormalizedAccount
    positions: tuple[NormalizedPosition, ...]
    open_orders: tuple[NormalizedOrder, ...]
    fills: tuple[NormalizedFill, ...] = ()
    closed_trades: tuple[NormalizedTrade, ...] = ()
    as_of: datetime = field(default_factory=utc_now)

    @property
    def total_cost_basis(self) -> Decimal:
        return sum((position.cost_basis for position in self.positions), Decimal("0"))

    @property
    def total_market_value(self) -> Decimal:
        return sum((position.market_value for position in self.positions), Decimal("0"))

    @property
    def total_unrealized_pnl(self) -> Decimal:
        return sum((position.unrealized_pnl for position in self.positions), Decimal("0"))

    @property
    def total_realized_pnl(self) -> Decimal:
        return sum((trade.realized_pnl for trade in self.closed_trades), Decimal("0"))

    @property
    def total_portfolio_value(self) -> Decimal:
        return self.account.cash + self.total_market_value

    @property
    def open_order_count(self) -> int:
        return len(self.open_orders)

    @property
    def is_flat(self) -> bool:
        return not self.positions and not self.open_orders


@dataclass(frozen=True, slots=True)
class BrokerReconciliationResult:
    account: NormalizedAccount
    positions: tuple[NormalizedPosition, ...]
    open_orders: tuple[NormalizedOrder, ...]
    fills: tuple[NormalizedFill, ...] = ()
    closed_trades: tuple[NormalizedTrade, ...] = ()
    completed_at: datetime = field(default_factory=utc_now)
    timeout_seconds: int = 30

    def portfolio_snapshot(self, market: Market) -> PortfolioSnapshot:
        return PortfolioSnapshot(
            market=market,
            account=self.account,
            positions=self.positions,
            open_orders=self.open_orders,
            fills=self.fills,
            closed_trades=self.closed_trades,
            as_of=self.completed_at,
        )