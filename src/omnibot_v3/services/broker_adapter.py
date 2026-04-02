"""Broker adapter protocol and contract test harness."""

from __future__ import annotations

from dataclasses import dataclass

from omnibot_v3.domain.broker import (
    BarTimeframe,
    BrokerCapability,
    BrokerEnvironment,
    BrokerHealth,
    BrokerMetadata,
    BrokerReconciliationResult,
    HistoricalBar,
    NormalizedAccount,
    NormalizedOrder,
    NormalizedPosition,
    NormalizedTrade,
    OrderRequest,
    OrderStatus,
)


class BrokerAdapter:
    def metadata(self) -> BrokerMetadata:
        raise NotImplementedError

    def health_check(self) -> BrokerHealth:
        raise NotImplementedError

    def get_account(self) -> NormalizedAccount:
        raise NotImplementedError

    def list_positions(self) -> list[NormalizedPosition]:
        raise NotImplementedError

    def get_latest_price(self, symbol: str):
        return None

    def get_latest_prices(self, symbols: tuple[str, ...] | list[str]):
        prices: dict[str, object] = {}
        for symbol in symbols:
            price = self.get_latest_price(symbol)
            if price is not None:
                prices[symbol] = price
        return prices

    def get_historical_bars(
        self,
        symbol: str,
        timeframe: BarTimeframe,
        *,
        limit: int = 100,
    ) -> tuple[HistoricalBar, ...]:
        del symbol, timeframe, limit
        return ()

    def list_closed_trades(self, *, limit: int = 100) -> tuple[NormalizedTrade, ...]:
        del limit
        return ()

    def submit_order(self, order_request: OrderRequest) -> NormalizedOrder:
        raise NotImplementedError

    def cancel_order(self, order_id: str) -> NormalizedOrder:
        raise NotImplementedError

    def get_order(self, order_id: str) -> NormalizedOrder | None:
        raise NotImplementedError

    def reconcile(self, timeout_seconds: int | None = None) -> BrokerReconciliationResult:
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class BrokerAdapterContractHarness:
    adapter: BrokerAdapter

    def verify(self, sample_order_request: OrderRequest) -> BrokerReconciliationResult:
        metadata = self.adapter.metadata()
        required_capabilities = {
            BrokerCapability.SUBMIT_ORDER,
            BrokerCapability.CANCEL_ORDER,
            BrokerCapability.QUERY_ORDER,
            BrokerCapability.RECONCILE,
            BrokerCapability.HEALTH_CHECK,
        }
        if not required_capabilities.issubset(metadata.capabilities):
            raise AssertionError("Broker adapter does not expose the required capabilities.")
        if not metadata.safety_policy.require_market_arming:
            raise AssertionError("Broker adapters must require explicit market arming.")
        if (
            metadata.environment == BrokerEnvironment.LIVE
            and not metadata.safety_policy.allow_live_execution
        ):
            raise AssertionError("Live adapters must declare live execution capability.")

        health = self.adapter.health_check()
        if not health.message:
            raise AssertionError("Broker health responses must include a diagnostic message.")

        account = self.adapter.get_account()
        if not account.account_id:
            raise AssertionError("Broker accounts must expose an account identifier.")

        positions = self.adapter.list_positions()
        if any(not position.symbol for position in positions):
            raise AssertionError("Normalized positions must include a symbol.")

        order = self.adapter.submit_order(sample_order_request)
        if order.client_order_id != sample_order_request.client_order_id:
            raise AssertionError("Submitted orders must preserve the client order id.")

        queried = self.adapter.get_order(order.order_id)
        if queried is None:
            raise AssertionError("Submitted orders must be queryable by order id.")

        canceled = self.adapter.cancel_order(order.order_id)
        if canceled.status not in {OrderStatus.CANCELED, OrderStatus.FILLED, OrderStatus.REJECTED}:
            raise AssertionError("Canceled orders must report a terminal order status.")

        policy = metadata.reconciliation_policy
        reconciliation = self.adapter.reconcile(timeout_seconds=policy.default_timeout_seconds)
        if reconciliation.account.account_id != account.account_id:
            raise AssertionError("Reconciliation must return the same normalized account.")
        if reconciliation.timeout_seconds > policy.hard_timeout_seconds:
            raise AssertionError("Reconciliation exceeded the declared hard timeout.")

        portfolio = reconciliation.portfolio_snapshot(metadata.market)
        if portfolio.market != metadata.market:
            raise AssertionError("Portfolio snapshots must be tagged to the adapter market.")
        if portfolio.account.account_id != reconciliation.account.account_id:
            raise AssertionError("Portfolio snapshots must retain the reconciled account.")
        if portfolio.open_orders != reconciliation.open_orders:
            raise AssertionError("Portfolio snapshots must retain reconciled open orders.")
        if portfolio.as_of != reconciliation.completed_at:
            raise AssertionError("Portfolio snapshots must use reconciliation completion time.")

        return reconciliation
