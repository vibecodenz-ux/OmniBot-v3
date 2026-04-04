"""Replay-focused broker adapter for walk-forward scanner validation."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from omnibot_v3.domain.broker import (
    BarTimeframe,
    BrokerCapability,
    BrokerEnvironment,
    BrokerHealth,
    BrokerHealthStatus,
    BrokerMetadata,
    BrokerReconciliationPolicy,
    BrokerReconciliationResult,
    BrokerSafetyPolicy,
    HistoricalBar,
    NormalizedAccount,
    NormalizedFill,
    NormalizedOrder,
    NormalizedPosition,
    NormalizedTrade,
    OrderRequest,
    OrderSide,
    OrderStatus,
)
from omnibot_v3.domain.runtime import Market
from omnibot_v3.infra.mock_broker import build_canned_account
from omnibot_v3.services.broker_adapter import BrokerAdapter


def _normalize_bars(
    bars_by_symbol: dict[str, tuple[HistoricalBar, ...] | list[HistoricalBar]],
) -> dict[str, tuple[HistoricalBar, ...]]:
    normalized: dict[str, tuple[HistoricalBar, ...]] = {}
    for symbol, bars in bars_by_symbol.items():
        ordered = tuple(sorted(tuple(bars), key=lambda item: item.opened_at))
        normalized[symbol.upper()] = ordered
    return normalized


@dataclass(slots=True)
class ReplayBrokerAdapter(BrokerAdapter):
    market: Market
    bars_by_symbol: dict[str, tuple[HistoricalBar, ...] | list[HistoricalBar]]
    environment: BrokerEnvironment = BrokerEnvironment.SANDBOX
    allow_live_execution: bool = False
    base_account: NormalizedAccount = field(default_factory=build_canned_account)
    _account: NormalizedAccount = field(init=False)
    _positions: dict[str, NormalizedPosition] = field(default_factory=dict, init=False)
    _orders: dict[str, NormalizedOrder] = field(default_factory=dict, init=False)
    _fills: list[NormalizedFill] = field(default_factory=list, init=False)
    _closed_trades: list[NormalizedTrade] = field(default_factory=list, init=False)
    _current_prices: dict[str, Decimal] = field(default_factory=dict, init=False)
    _bars: dict[str, tuple[HistoricalBar, ...]] = field(init=False)
    _current_time: datetime | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        self._bars = _normalize_bars(self.bars_by_symbol)
        self._account = self.base_account

    def metadata(self) -> BrokerMetadata:
        return BrokerMetadata(
            adapter_name="replay-broker",
            market=self.market,
            environment=self.environment,
            capabilities=frozenset(
                {
                    BrokerCapability.SUBMIT_ORDER,
                    BrokerCapability.CANCEL_ORDER,
                    BrokerCapability.QUERY_ORDER,
                    BrokerCapability.RECONCILE,
                    BrokerCapability.HEALTH_CHECK,
                    BrokerCapability.HISTORICAL_BARS,
                    BrokerCapability.TRADE_HISTORY,
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
        return BrokerHealth(status=BrokerHealthStatus.HEALTHY, message="replay adapter healthy")

    def advance_to(self, observed_at: datetime) -> None:
        self._current_time = observed_at
        current_prices: dict[str, Decimal] = {}
        for symbol, bars in self._bars.items():
            latest_bar = next((bar for bar in reversed(bars) if bar.closed_at <= observed_at), None)
            if latest_bar is not None:
                current_prices[symbol] = latest_bar.close_price
        self._current_prices = current_prices

        updated_positions: dict[str, NormalizedPosition] = {}
        for symbol, position in self._positions.items():
            market_price = current_prices.get(symbol.upper(), position.market_price)
            updated_positions[symbol] = NormalizedPosition(
                symbol=position.symbol,
                quantity=position.quantity,
                average_price=position.average_price,
                market_price=market_price,
                updated_at=observed_at,
            )
        self._positions = updated_positions
        self._refresh_account(observed_at)

    def seed_position(
        self,
        *,
        symbol: str,
        quantity: Decimal,
        average_price: Decimal,
        opened_at: datetime,
    ) -> None:
        normalized_symbol = symbol.strip().upper()
        market_price = self._current_prices.get(normalized_symbol, average_price)
        self._positions[normalized_symbol] = NormalizedPosition(
            symbol=symbol,
            quantity=quantity,
            average_price=average_price,
            market_price=market_price,
            updated_at=opened_at,
        )
        self._refresh_account(opened_at)

    def get_account(self) -> NormalizedAccount:
        return self._account

    def list_positions(self) -> list[NormalizedPosition]:
        return list(self._positions.values())

    def get_latest_price(self, symbol: str) -> Decimal | None:
        return self._current_prices.get(symbol.strip().upper())

    def get_historical_bars(
        self,
        symbol: str,
        timeframe: BarTimeframe,
        *,
        limit: int = 100,
    ) -> tuple[HistoricalBar, ...]:
        bars = [
            bar
            for bar in self._bars.get(symbol.strip().upper(), ())
            if bar.timeframe == timeframe and (self._current_time is None or bar.closed_at <= self._current_time)
        ]
        return tuple(bars[-max(limit, 0) :])

    def list_closed_trades(self, *, limit: int = 100) -> tuple[NormalizedTrade, ...]:
        return tuple(self._closed_trades[-max(limit, 0) :])

    def submit_order(self, order_request: OrderRequest) -> NormalizedOrder:
        symbol = order_request.symbol.strip().upper()
        fill_price = self.get_latest_price(symbol) or order_request.limit_price
        if fill_price is None or fill_price <= Decimal("0"):
            raise ValueError(f"No replay price available for {order_request.symbol}.")

        executed_at = self._current_time or datetime.now(UTC)
        signed_quantity = order_request.quantity if order_request.side == OrderSide.BUY else -order_request.quantity
        existing = self._positions.get(symbol)
        order_id = f"order-{len(self._orders) + 1}"
        order = NormalizedOrder(
            order_id=order_id,
            client_order_id=order_request.client_order_id,
            symbol=order_request.symbol,
            side=order_request.side,
            quantity=order_request.quantity,
            filled_quantity=order_request.quantity,
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
                quantity=order_request.quantity,
                price=fill_price,
                executed_at=executed_at,
            )
        )

        if existing is None or existing.quantity == Decimal("0") or existing.quantity * signed_quantity > Decimal("0"):
            self._open_or_extend_position(symbol, order_request.symbol, signed_quantity, fill_price, executed_at)
        else:
            self._reduce_or_flip_position(existing, order_request.symbol, signed_quantity, fill_price, executed_at)

        self._refresh_account(executed_at)
        return order

    def cancel_order(self, order_id: str) -> NormalizedOrder:
        order = self._orders[order_id]
        if order.status == OrderStatus.FILLED:
            return order
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
        completed_at = self._current_time or datetime.now(UTC)
        self._refresh_account(completed_at)
        return BrokerReconciliationResult(
            account=self._account,
            positions=tuple(self.list_positions()),
            open_orders=(),
            fills=tuple(self._fills),
            closed_trades=tuple(self._closed_trades),
            completed_at=completed_at,
            timeout_seconds=effective_timeout,
        )

    def _open_or_extend_position(
        self,
        symbol: str,
        raw_symbol: str,
        signed_quantity: Decimal,
        fill_price: Decimal,
        executed_at: datetime,
    ) -> None:
        existing = self._positions.get(symbol)
        if existing is None:
            self._positions[symbol] = NormalizedPosition(
                symbol=raw_symbol,
                quantity=signed_quantity,
                average_price=fill_price,
                market_price=fill_price,
                updated_at=executed_at,
            )
            return

        existing_abs = abs(existing.quantity)
        added_abs = abs(signed_quantity)
        combined_abs = existing_abs + added_abs
        weighted_average = (
            (existing.average_price * existing_abs) + (fill_price * added_abs)
        ) / combined_abs
        self._positions[symbol] = NormalizedPosition(
            symbol=existing.symbol,
            quantity=existing.quantity + signed_quantity,
            average_price=weighted_average,
            market_price=fill_price,
            updated_at=executed_at,
        )

    def _reduce_or_flip_position(
        self,
        existing: NormalizedPosition,
        raw_symbol: str,
        signed_quantity: Decimal,
        fill_price: Decimal,
        executed_at: datetime,
    ) -> None:
        symbol = existing.symbol.strip().upper()
        closing_quantity = min(abs(existing.quantity), abs(signed_quantity))
        closing_side = OrderSide.BUY if existing.quantity >= Decimal("0") else OrderSide.SELL
        self._closed_trades.append(
            NormalizedTrade(
                trade_id=f"trade-{uuid4().hex[:12]}",
                market=self.market,
                symbol=existing.symbol,
                side=closing_side,
                quantity=closing_quantity,
                entry_price=existing.average_price,
                exit_price=fill_price,
                opened_at=existing.updated_at,
                closed_at=executed_at,
            )
        )

        remaining_quantity = existing.quantity + signed_quantity
        if remaining_quantity == Decimal("0"):
            self._positions.pop(symbol, None)
            return

        if existing.quantity * remaining_quantity > Decimal("0"):
            self._positions[symbol] = NormalizedPosition(
                symbol=existing.symbol,
                quantity=remaining_quantity,
                average_price=existing.average_price,
                market_price=fill_price,
                updated_at=executed_at,
            )
            return

        self._positions[symbol] = NormalizedPosition(
            symbol=raw_symbol,
            quantity=remaining_quantity,
            average_price=fill_price,
            market_price=fill_price,
            updated_at=executed_at,
        )

    def _refresh_account(self, updated_at: datetime) -> None:
        cash = self.base_account.cash
        for fill in self._fills:
            if fill.side == OrderSide.BUY:
                cash -= fill.notional + fill.commission
            else:
                cash += fill.notional - fill.commission
        market_value = sum((position.market_value for position in self._positions.values()), Decimal("0"))
        equity = cash + market_value
        self._account = NormalizedAccount(
            account_id=self.base_account.account_id,
            currency=self.base_account.currency,
            equity=equity,
            buying_power=max(equity * Decimal("2"), Decimal("0")),
            cash=cash,
            updated_at=updated_at,
        )