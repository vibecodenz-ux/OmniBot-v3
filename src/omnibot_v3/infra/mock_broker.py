"""Mock broker adapter and canned responses for contract tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from omnibot_v3.domain.broker import (
    BrokerCapability,
    BrokerEnvironment,
    BrokerHealth,
    BrokerHealthStatus,
    BrokerMetadata,
    BrokerReconciliationPolicy,
    BrokerReconciliationResult,
    BrokerSafetyPolicy,
    NormalizedAccount,
    NormalizedFill,
    NormalizedOrder,
    NormalizedPosition,
    NormalizedTrade,
    OrderRequest,
    OrderSide,
    OrderStatus,
    OrderType,
    TimeInForce,
)
from omnibot_v3.domain.runtime import Market
from omnibot_v3.services.broker_adapter import BrokerAdapter


def build_canned_account() -> NormalizedAccount:
    return NormalizedAccount(
        account_id="SIM-001",
        currency="USD",
        equity=Decimal("100000.00"),
        buying_power=Decimal("200000.00"),
        cash=Decimal("50000.00"),
    )


def build_canned_positions() -> list[NormalizedPosition]:
    return [
        NormalizedPosition(
            symbol="AAPL",
            quantity=Decimal("10"),
            average_price=Decimal("185.00"),
            market_price=Decimal("190.00"),
        )
    ]


def build_canned_short_positions() -> list[NormalizedPosition]:
    return [
        NormalizedPosition(
            symbol="BTC/USD",
            quantity=Decimal("-0.5"),
            average_price=Decimal("65000.00"),
            market_price=Decimal("64000.00"),
        )
    ]


def build_canned_fills() -> list[NormalizedFill]:
    executed_at = datetime(2024, 1, 2, 14, 30, tzinfo=UTC)
    return [
        NormalizedFill(
            fill_id="fill-001",
            order_id="hist-order-001",
            client_order_id="hist-client-001",
            symbol="AAPL",
            side=OrderSide.BUY,
            quantity=Decimal("10"),
            price=Decimal("185.00"),
            commission=Decimal("1.25"),
            executed_at=executed_at,
        )
    ]


def build_canned_closed_trades(market: Market) -> list[NormalizedTrade]:
    opened_at = datetime(2024, 1, 2, 14, 30, tzinfo=UTC)
    closed_at = datetime(2024, 1, 5, 20, 0, tzinfo=UTC)
    return [
        NormalizedTrade(
            trade_id="trade-001",
            market=market,
            symbol="MSFT",
            side=OrderSide.BUY,
            quantity=Decimal("5"),
            entry_price=Decimal("410.00"),
            exit_price=Decimal("418.00"),
            opened_at=opened_at,
            closed_at=closed_at,
            fees=Decimal("2.50"),
        )
    ]


def build_canned_order_request() -> OrderRequest:
    return OrderRequest(
        client_order_id="test-order-001",
        symbol="AAPL",
        side=OrderSide.BUY,
        quantity=Decimal("1"),
        order_type=OrderType.LIMIT,
        time_in_force=TimeInForce.DAY,
        limit_price=Decimal("189.50"),
    )


@dataclass(slots=True)
class MockBrokerAdapter(BrokerAdapter):
    market: Market = Market.STOCKS
    environment: BrokerEnvironment = BrokerEnvironment.SANDBOX
    allow_live_execution: bool = False
    _account: NormalizedAccount = field(default_factory=build_canned_account)
    _positions: list[NormalizedPosition] = field(default_factory=build_canned_positions)
    _fills: list[NormalizedFill] = field(default_factory=build_canned_fills)
    _closed_trades: list[NormalizedTrade] = field(default_factory=list)
    _orders: dict[str, NormalizedOrder] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self._closed_trades:
            self._closed_trades = build_canned_closed_trades(self.market)
        if self.market == Market.CRYPTO and all(
            position.symbol.upper() != "BTC/USD" for position in self._positions
        ):
            self._positions = build_canned_short_positions()

    def metadata(self) -> BrokerMetadata:
        return BrokerMetadata(
            adapter_name="mock-broker",
            market=self.market,
            environment=self.environment,
            capabilities=frozenset(
                {
                    BrokerCapability.SUBMIT_ORDER,
                    BrokerCapability.CANCEL_ORDER,
                    BrokerCapability.QUERY_ORDER,
                    BrokerCapability.RECONCILE,
                    BrokerCapability.HEALTH_CHECK,
                }
            ),
            safety_policy=BrokerSafetyPolicy(
                require_market_arming=True,
                allow_live_execution=self.allow_live_execution,
                supports_kill_switch=True,
            ),
            reconciliation_policy=BrokerReconciliationPolicy(
                default_timeout_seconds=10,
                hard_timeout_seconds=30,
            ),
        )

    def health_check(self) -> BrokerHealth:
        return BrokerHealth(status=BrokerHealthStatus.HEALTHY, message="mock adapter healthy")

    def get_account(self) -> NormalizedAccount:
        return self._account

    def list_positions(self) -> list[NormalizedPosition]:
        return list(self._positions)

    def get_latest_price(self, symbol: str) -> Decimal | None:
        normalized = symbol.strip().upper()
        position = next((item for item in self._positions if item.symbol.upper() == normalized), None)
        if position is not None:
            return position.market_price
        defaults = {
            (Market.STOCKS, "SPY"): Decimal("520.00"),
            (Market.STOCKS, "QQQ"): Decimal("444.00"),
            (Market.STOCKS, "AAPL"): Decimal("190.00"),
            (Market.STOCKS, "MSFT"): Decimal("418.00"),
            (Market.STOCKS, "NVDA"): Decimal("902.00"),
            (Market.STOCKS, "AMZN"): Decimal("186.00"),
            (Market.STOCKS, "META"): Decimal("498.00"),
            (Market.STOCKS, "GOOGL"): Decimal("154.00"),
            (Market.STOCKS, "AMD"): Decimal("178.00"),
            (Market.STOCKS, "AVGO"): Decimal("1325.00"),
            (Market.STOCKS, "TSLA"): Decimal("171.00"),
            (Market.STOCKS, "NFLX"): Decimal("612.00"),
            (Market.STOCKS, "PLTR"): Decimal("23.50"),
            (Market.STOCKS, "IWM"): Decimal("207.00"),
            (Market.STOCKS, "SMCI"): Decimal("925.00"),
            (Market.CRYPTO, "BTC/USD"): Decimal("64000.00"),
            (Market.CRYPTO, "BTC/USDT"): Decimal("64000.00"),
            (Market.CRYPTO, "ETH/USD"): Decimal("3000.00"),
            (Market.CRYPTO, "ETH/USDT"): Decimal("3000.00"),
            (Market.CRYPTO, "SOL/USDT"): Decimal("185.00"),
            (Market.CRYPTO, "BNB/USDT"): Decimal("585.00"),
            (Market.CRYPTO, "XRP/USDT"): Decimal("0.62"),
            (Market.CRYPTO, "ADA/USDT"): Decimal("0.74"),
            (Market.CRYPTO, "DOGE/USDT"): Decimal("0.18"),
            (Market.CRYPTO, "LINK/USDT"): Decimal("19.40"),
            (Market.CRYPTO, "AVAX/USDT"): Decimal("42.50"),
            (Market.CRYPTO, "LTC/USDT"): Decimal("84.20"),
            (Market.CRYPTO, "BCH/USDT"): Decimal("515.00"),
            (Market.CRYPTO, "SUI/USDT"): Decimal("1.72"),
            (Market.FOREX, "EURUSD"): Decimal("1.0860"),
            (Market.FOREX, "GBPUSD"): Decimal("1.2720"),
            (Market.FOREX, "USDJPY"): Decimal("151.20"),
            (Market.FOREX, "AUDUSD"): Decimal("0.6610"),
            (Market.FOREX, "USDCHF"): Decimal("0.9030"),
            (Market.FOREX, "USDCAD"): Decimal("1.3530"),
            (Market.FOREX, "NZDUSD"): Decimal("0.6080"),
            (Market.FOREX, "EURGBP"): Decimal("0.8520"),
            (Market.FOREX, "EURJPY"): Decimal("163.55"),
            (Market.FOREX, "GBPJPY"): Decimal("192.30"),
            (Market.FOREX, "EURCHF"): Decimal("0.9545"),
            (Market.FOREX, "AUDJPY"): Decimal("99.95"),
        }
        return defaults.get((self.market, normalized))

    def submit_order(self, order_request: OrderRequest) -> NormalizedOrder:
        position = next(
            (item for item in self._positions if item.symbol.upper() == order_request.symbol.upper()),
            None,
        )
        if (
            order_request.order_type == OrderType.MARKET
            and position is not None
            and _is_closing_order(position, order_request)
        ):
            return self._submit_closing_order(order_request, position)

        order_id = f"order-{len(self._orders) + 1}"
        order = NormalizedOrder(
            order_id=order_id,
            client_order_id=order_request.client_order_id,
            symbol=order_request.symbol,
            side=order_request.side,
            quantity=order_request.quantity,
            filled_quantity=Decimal("0"),
            order_type=order_request.order_type,
            status=OrderStatus.ACCEPTED,
            time_in_force=order_request.time_in_force,
            limit_price=order_request.limit_price,
        )
        self._orders[order_id] = order
        return order

    def cancel_order(self, order_id: str) -> NormalizedOrder:
        order = self._orders[order_id]
        canceled = NormalizedOrder(
            order_id=order.order_id,
            client_order_id=order.client_order_id,
            symbol=order.symbol,
            side=order.side,
            quantity=order.quantity,
            filled_quantity=order.filled_quantity,
            order_type=order.order_type,
            status=OrderStatus.CANCELED,
            time_in_force=order.time_in_force,
            limit_price=order.limit_price,
            average_fill_price=order.average_fill_price,
            submitted_at=order.submitted_at,
        )
        self._orders[order_id] = canceled
        return canceled

    def get_order(self, order_id: str) -> NormalizedOrder | None:
        return self._orders.get(order_id)

    def reconcile(self, timeout_seconds: int | None = None) -> BrokerReconciliationResult:
        policy = self.metadata().reconciliation_policy
        effective_timeout = timeout_seconds or policy.default_timeout_seconds
        if effective_timeout > policy.hard_timeout_seconds:
            raise ValueError("Requested reconciliation timeout exceeds the hard timeout.")

        open_orders = tuple(
            order for order in self._orders.values() if order.status in {OrderStatus.ACCEPTED, OrderStatus.OPEN}
        )
        return BrokerReconciliationResult(
            account=self._account,
            positions=tuple(self._positions),
            open_orders=open_orders,
            fills=tuple(self._fills),
            closed_trades=tuple(self._closed_trades),
            timeout_seconds=effective_timeout,
        )

    def _submit_closing_order(
        self,
        order_request: OrderRequest,
        position: NormalizedPosition,
    ) -> NormalizedOrder:
        order_id = f"order-{len(self._orders) + 1}"
        fill_price = position.market_price
        executed_at = datetime.now(UTC)
        fill_quantity = min(abs(position.quantity), order_request.quantity)
        order = NormalizedOrder(
            order_id=order_id,
            client_order_id=order_request.client_order_id,
            symbol=order_request.symbol,
            side=order_request.side,
            quantity=order_request.quantity,
            filled_quantity=fill_quantity,
            order_type=order_request.order_type,
            status=OrderStatus.FILLED,
            time_in_force=order_request.time_in_force,
            limit_price=order_request.limit_price,
            average_fill_price=fill_price,
            submitted_at=executed_at,
        )
        self._orders[order_id] = order

        self._fills.append(
            NormalizedFill(
                fill_id=f"fill-{uuid4().hex[:12]}",
                order_id=order.order_id,
                client_order_id=order.client_order_id,
                symbol=order.symbol,
                side=order.side,
                quantity=fill_quantity,
                price=fill_price,
                executed_at=executed_at,
            )
        )
        self._closed_trades.append(
            NormalizedTrade(
                trade_id=f"trade-{uuid4().hex[:12]}",
                market=self.market,
                symbol=position.symbol,
                side=OrderSide.BUY if position.quantity >= 0 else OrderSide.SELL,
                quantity=fill_quantity,
                entry_price=position.average_price,
                exit_price=fill_price,
                opened_at=position.updated_at,
                closed_at=executed_at,
            )
        )

        remaining_quantity = abs(position.quantity) - fill_quantity
        updated_positions: list[NormalizedPosition] = []
        for existing in self._positions:
            if existing.symbol.upper() != position.symbol.upper():
                updated_positions.append(existing)
                continue

            if remaining_quantity <= Decimal("0"):
                continue

            signed_quantity = remaining_quantity if position.quantity >= 0 else -remaining_quantity
            updated_positions.append(
                NormalizedPosition(
                    symbol=existing.symbol,
                    quantity=signed_quantity,
                    average_price=existing.average_price,
                    market_price=existing.market_price,
                    updated_at=executed_at,
                )
            )
        self._positions = updated_positions

        cash_delta = fill_price * fill_quantity
        updated_cash = (
            self._account.cash + cash_delta
            if order.side == OrderSide.SELL
            else self._account.cash - cash_delta
        )
        self._account = NormalizedAccount(
            account_id=self._account.account_id,
            currency=self._account.currency,
            equity=self._account.equity,
            buying_power=self._account.buying_power,
            cash=updated_cash,
            updated_at=executed_at,
        )
        return order


def _is_closing_order(position: NormalizedPosition, order_request: OrderRequest) -> bool:
    if position.quantity > 0:
        return order_request.side == OrderSide.SELL and order_request.quantity > 0
    if position.quantity < 0:
        return order_request.side == OrderSide.BUY and order_request.quantity > 0
    return False