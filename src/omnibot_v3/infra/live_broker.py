"""Live and unconfigured broker adapters used by the shipped dashboard."""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from types import MappingProxyType
from typing import TYPE_CHECKING, cast
from urllib import error, parse, request

from omnibot_v3.domain import (
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
    Market,
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
from omnibot_v3.domain.config import AppConfig
from omnibot_v3.services.broker_adapter import BrokerAdapter
from omnibot_v3.services.market_catalog import CRYPTO_SYMBOLS, FOREX_SYMBOLS, STOCK_SYMBOLS
from omnibot_v3.services.secret_api import SecretRegistry
from omnibot_v3.services.secrets import SecretAccessError, SecretStoreService

if TYPE_CHECKING:
    from omnibot_v3.services.market_worker import MarketWorker

_ZERO = Decimal("0")
_STABLE_ASSETS = frozenset({"USDT", "USDC", "BUSD", "FDUSD", "TUSD", "USDP"})
_BINANCE_FUTURES_DEMO_BASE_URL = "https://demo-fapi.binance.com"

ALPACA_API_KEY_SECRET_ID = "alpaca-api-key"
ALPACA_API_SECRET_SECRET_ID = "alpaca-api-secret"
BINANCE_API_KEY_SECRET_ID = "binance-api-key"
BINANCE_API_SECRET_SECRET_ID = "binance-api-secret"
IG_USERNAME_SECRET_ID = "ig-forex-au-username"
IG_PASSWORD_SECRET_ID = "ig-forex-au-password"
IG_API_KEY_SECRET_ID = "ig-forex-au-api-key"
_IG_FOREX_EPICS = {
    "EURUSD": "CS.D.EURUSD.CFD.IP",
    "GBPUSD": "CS.D.GBPUSD.CFD.IP",
    "USDJPY": "CS.D.USDJPY.CFD.IP",
    "AUDUSD": "CS.D.AUDUSD.CFD.IP",
    "USDCHF": "CS.D.USDCHF.CFD.IP",
    "USDCAD": "CS.D.USDCAD.CFD.IP",
    "NZDUSD": "CS.D.NZDUSD.CFD.IP",
    "EURGBP": "CS.D.EURGBP.CFD.IP",
    "EURJPY": "CS.D.EURJPY.CFD.IP",
    "GBPJPY": "CS.D.GBPJPY.CFD.IP",
    "EURCHF": "CS.D.EURCHF.CFD.IP",
    "AUDJPY": "CS.D.AUDJPY.CFD.IP",
}


@dataclass(frozen=True, slots=True)
class BinanceSymbolRules:
    min_qty: Decimal
    step_size: Decimal
    min_notional: Decimal = _ZERO


def build_live_market_workers(
    config: AppConfig,
    registry: SecretRegistry,
    store_service: SecretStoreService,
) -> dict[Market, MarketWorker]:
    from omnibot_v3.domain.worker import MarketWorkerSettings
    from omnibot_v3.services.market_integrations import CryptoWorker, ForexWorker, StocksWorker

    resolver = BrokerSecretResolver(registry=registry, store_service=store_service)

    stocks_adapter = build_stocks_adapter(config, resolver)
    crypto_adapter = build_crypto_adapter(resolver)
    forex_adapter = build_forex_adapter(resolver)

    return {
        Market.STOCKS: StocksWorker(
            settings=MarketWorkerSettings(
                market=Market.STOCKS,
                environment=stocks_adapter.metadata().environment,
                broker_adapter_name=stocks_adapter.metadata().adapter_name,
                symbols=STOCK_SYMBOLS,
                allow_live_execution=not config.broker_paper_trading,
                poll_interval_seconds=15,
            ),
            adapter=stocks_adapter,
        ),
        Market.CRYPTO: CryptoWorker(
            settings=MarketWorkerSettings(
                market=Market.CRYPTO,
                environment=crypto_adapter.metadata().environment,
                broker_adapter_name=crypto_adapter.metadata().adapter_name,
                symbols=CRYPTO_SYMBOLS,
                allow_live_execution=False,
                poll_interval_seconds=10,
            ),
            adapter=crypto_adapter,
        ),
        Market.FOREX: ForexWorker(
            settings=MarketWorkerSettings(
                market=Market.FOREX,
                environment=forex_adapter.metadata().environment,
                broker_adapter_name=forex_adapter.metadata().adapter_name,
                symbols=FOREX_SYMBOLS,
                allow_live_execution=False,
                poll_interval_seconds=30,
            ),
            adapter=forex_adapter,
        ),
    }


@dataclass(frozen=True, slots=True)
class BrokerSecretResolver:
    registry: SecretRegistry
    store_service: SecretStoreService

    def resolve(self, secret_id: str) -> str | None:
        metadata = self.registry.get(secret_id)
        if metadata is None:
            return None
        try:
            value = self.store_service.resolve_secret(metadata)
        except SecretAccessError:
            return None
        normalized = value.strip()
        return normalized or None


@dataclass(frozen=True, slots=True)
class UnconfiguredBrokerAdapter(BrokerAdapter):
    market: Market
    adapter_name: str
    environment: BrokerEnvironment
    reason: str

    def metadata(self) -> BrokerMetadata:
        return BrokerMetadata(
            adapter_name=self.adapter_name,
            market=self.market,
            environment=self.environment,
            capabilities=frozenset({BrokerCapability.RECONCILE, BrokerCapability.HEALTH_CHECK}),
            safety_policy=BrokerSafetyPolicy(require_market_arming=True, allow_live_execution=False),
            reconciliation_policy=BrokerReconciliationPolicy(default_timeout_seconds=15, hard_timeout_seconds=30),
        )

    def configuration_errors(self) -> tuple[str, ...]:
        return (self.reason,)

    def health_check(self) -> BrokerHealth:
        return BrokerHealth(status=BrokerHealthStatus.UNHEALTHY, message=self.reason)

    def get_account(self) -> NormalizedAccount:
        return NormalizedAccount(account_id="unconfigured", currency="USD", equity=_ZERO, buying_power=_ZERO, cash=_ZERO)

    def list_positions(self) -> list[NormalizedPosition]:
        return []

    def submit_order(self, order_request: OrderRequest) -> NormalizedOrder:
        raise ValueError(self.reason)

    def cancel_order(self, order_id: str) -> NormalizedOrder:
        raise ValueError(self.reason)

    def get_order(self, order_id: str) -> NormalizedOrder | None:
        return None

    def reconcile(self, timeout_seconds: int | None = None) -> BrokerReconciliationResult:
        raise ValueError(self.reason)


@dataclass(frozen=True, slots=True)
class AlpacaBrokerAdapter(BrokerAdapter):
    api_key: str
    api_secret: str
    paper_trading: bool = True
    timeout_seconds: int = 15

    def metadata(self) -> BrokerMetadata:
        return BrokerMetadata(
            adapter_name="alpaca",
            market=Market.STOCKS,
            environment=BrokerEnvironment.SANDBOX if self.paper_trading else BrokerEnvironment.LIVE,
            capabilities=frozenset(
                {
                    BrokerCapability.SUBMIT_ORDER,
                    BrokerCapability.CANCEL_ORDER,
                    BrokerCapability.QUERY_ORDER,
                    BrokerCapability.RECONCILE,
                    BrokerCapability.HEALTH_CHECK,
                    BrokerCapability.HISTORICAL_BARS,
                }
            ),
            safety_policy=BrokerSafetyPolicy(require_market_arming=True, allow_live_execution=not self.paper_trading),
            reconciliation_policy=BrokerReconciliationPolicy(default_timeout_seconds=15, hard_timeout_seconds=30),
        )

    def health_check(self) -> BrokerHealth:
        try:
            account = self.get_account()
        except Exception as exc:
            return BrokerHealth(status=BrokerHealthStatus.UNHEALTHY, message=str(exc))
        return BrokerHealth(status=BrokerHealthStatus.HEALTHY, message=f"connected to Alpaca account {account.account_id}")

    def get_account(self) -> NormalizedAccount:
        payload = self._request_json("GET", "/v2/account")
        if not isinstance(payload, dict):
            raise ValueError("Alpaca account payload is invalid.")
        return NormalizedAccount(
            account_id=str(payload.get("account_number") or payload.get("id") or "alpaca-account"),
            currency=str(payload.get("currency") or "USD"),
            equity=_decimal(payload.get("equity")),
            buying_power=_decimal(payload.get("buying_power")),
            cash=_decimal(payload.get("cash")),
        )

    def list_positions(self) -> list[NormalizedPosition]:
        payload = self._request_json("GET", "/v2/positions")
        if not isinstance(payload, list):
            raise ValueError("Alpaca positions payload is invalid.")
        positions: list[NormalizedPosition] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            positions.append(
                NormalizedPosition(
                    symbol=str(item.get("symbol") or ""),
                    quantity=_decimal(item.get("qty")),
                    average_price=_decimal(item.get("avg_entry_price")),
                    market_price=_decimal(item.get("current_price")),
                )
            )
        return positions

    def get_latest_price(self, symbol: str) -> Decimal | None:
        payload = _http_json_request(
            f"https://data.alpaca.markets/v2/stocks/{symbol.upper()}/trades/latest",
            method="GET",
            headers={
                "APCA-API-KEY-ID": self.api_key,
                "APCA-API-SECRET-KEY": self.api_secret,
                "Accept": "application/json",
            },
            timeout_seconds=self.timeout_seconds,
        )
        if not isinstance(payload, dict):
            return None
        trade = _as_object_dict(payload.get("trade"))
        return _optional_decimal(trade.get("p"))

    def get_latest_prices(self, symbols: tuple[str, ...] | list[str]) -> dict[str, Decimal]:
        normalized_symbols = [str(symbol).upper() for symbol in symbols if str(symbol).strip()]
        if not normalized_symbols:
            return {}
        query = parse.urlencode({"symbols": ",".join(normalized_symbols)})
        payload = _http_json_request(
            f"https://data.alpaca.markets/v2/stocks/snapshots?{query}",
            method="GET",
            headers={
                "APCA-API-KEY-ID": self.api_key,
                "APCA-API-SECRET-KEY": self.api_secret,
                "Accept": "application/json",
            },
            timeout_seconds=self.timeout_seconds,
        )
        if not isinstance(payload, dict):
            return {}
        snapshots = _as_object_dict(payload.get("snapshots"))
        prices: dict[str, Decimal] = {}
        for symbol in normalized_symbols:
            price = _alpaca_snapshot_price(snapshots.get(symbol))
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
        normalized_symbol = str(symbol).upper().strip()
        if not normalized_symbol:
            return ()
        query = parse.urlencode(
            {
                "symbols": normalized_symbol,
                "timeframe": _alpaca_bar_timeframe(timeframe),
                "limit": str(max(limit, 1)),
                "adjustment": "raw",
            }
        )
        payload = _http_json_request(
            f"https://data.alpaca.markets/v2/stocks/bars?{query}",
            method="GET",
            headers={
                "APCA-API-KEY-ID": self.api_key,
                "APCA-API-SECRET-KEY": self.api_secret,
                "Accept": "application/json",
            },
            timeout_seconds=self.timeout_seconds,
        )
        return _alpaca_bars_to_domain(payload, normalized_symbol, timeframe)

    def submit_order(self, order_request: OrderRequest) -> NormalizedOrder:
        if order_request.order_type != OrderType.MARKET:
            raise ValueError("Only market orders are supported for Alpaca close actions.")
        payload: dict[str, object] = {
            "symbol": order_request.symbol,
            "qty": _decimal_to_string(order_request.quantity),
            "side": order_request.side.value,
            "type": "market",
            "time_in_force": _alpaca_time_in_force(order_request.time_in_force),
            "client_order_id": order_request.client_order_id,
        }
        response = self._request_json("POST", "/v2/orders", payload)
        return _alpaca_order_to_domain(response)

    def cancel_order(self, order_id: str) -> NormalizedOrder:
        response = self._request_json("DELETE", f"/v2/orders/{order_id}")
        return _alpaca_order_to_domain(response)

    def get_order(self, order_id: str) -> NormalizedOrder | None:
        response = self._request_json("GET", f"/v2/orders/{order_id}")
        if not isinstance(response, dict):
            return None
        return _alpaca_order_to_domain(response)

    def reconcile(self, timeout_seconds: int | None = None) -> BrokerReconciliationResult:
        account = self.get_account()
        positions = tuple(self.list_positions())
        orders_payload = self._request_json("GET", "/v2/orders?status=open&direction=desc")
        open_orders = tuple(_alpaca_order_to_domain(item) for item in orders_payload if isinstance(item, dict)) if isinstance(orders_payload, list) else ()
        return BrokerReconciliationResult(
            account=account,
            positions=positions,
            open_orders=open_orders,
            timeout_seconds=timeout_seconds or self.metadata().reconciliation_policy.default_timeout_seconds,
        )

    def _request_json(self, method: str, path: str, payload: dict[str, object] | None = None) -> object:
        base_url = "https://paper-api.alpaca.markets" if self.paper_trading else "https://api.alpaca.markets"
        headers = {
            "APCA-API-KEY-ID": self.api_key,
            "APCA-API-SECRET-KEY": self.api_secret,
            "Accept": "application/json",
        }
        return _http_json_request(base_url + path, method=method, headers=headers, payload=payload, timeout_seconds=self.timeout_seconds)


@dataclass(slots=True)
class BinanceBrokerAdapter(BrokerAdapter):
    api_key: str
    api_secret: str
    timeout_seconds: int = 15
    _order_symbols: dict[str, str] = field(default_factory=dict)
    _server_time_offset_ms: int = 0
    _symbol_rules: dict[str, BinanceSymbolRules] = field(default_factory=dict)

    def metadata(self) -> BrokerMetadata:
        return BrokerMetadata(
            adapter_name="binance",
            market=Market.CRYPTO,
            environment=BrokerEnvironment.SANDBOX,
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
            safety_policy=BrokerSafetyPolicy(require_market_arming=True, allow_live_execution=False),
            reconciliation_policy=BrokerReconciliationPolicy(default_timeout_seconds=15, hard_timeout_seconds=30),
        )

    def configuration_errors(self) -> tuple[str, ...]:
        return ()

    def health_check(self) -> BrokerHealth:
        try:
            account = self.get_account()
        except Exception as exc:
            return BrokerHealth(status=BrokerHealthStatus.UNHEALTHY, message=str(exc))
        return BrokerHealth(
            status=BrokerHealthStatus.HEALTHY,
            message=f"connected to Binance Futures demo account {account.account_id}",
        )

    def get_account(self) -> NormalizedAccount:
        account_payload = self._signed_request("GET", "/fapi/v3/account", {})
        if not isinstance(account_payload, dict):
            raise ValueError("Binance account payload is invalid.")
        return NormalizedAccount(
            account_id=str(account_payload.get("accountAlias") or "binance-futures-demo"),
            currency="USD",
            equity=_decimal(account_payload.get("totalMarginBalance") or account_payload.get("totalWalletBalance")),
            buying_power=_decimal(account_payload.get("availableBalance")),
            cash=_decimal(account_payload.get("availableBalance")),
        )

    def list_positions(self) -> list[NormalizedPosition]:
        account_payload = self._signed_request("GET", "/fapi/v3/account", {})
        return _binance_futures_positions(account_payload)

    def get_latest_price(self, symbol: str) -> Decimal | None:
        payload = _http_json_request(
            f"{_BINANCE_FUTURES_DEMO_BASE_URL}/fapi/v1/ticker/price?symbol={_binance_api_symbol(symbol)}",
            method="GET",
            headers={"Accept": "application/json"},
            timeout_seconds=self.timeout_seconds,
        )
        if not isinstance(payload, dict):
            return None
        return _optional_decimal(payload.get("price"))

    def get_latest_prices(self, symbols: tuple[str, ...] | list[str]) -> dict[str, Decimal]:
        normalized_symbols = {_binance_api_symbol(symbol): symbol for symbol in symbols if str(symbol).strip()}
        if not normalized_symbols:
            return {}
        payload = _http_json_request(
            f"{_BINANCE_FUTURES_DEMO_BASE_URL}/fapi/v1/ticker/price",
            method="GET",
            headers={"Accept": "application/json"},
            timeout_seconds=self.timeout_seconds,
        )
        if not isinstance(payload, list):
            return {}
        prices: dict[str, Decimal] = {}
        for item in payload:
            if not isinstance(item, dict):
                continue
            api_symbol = str(item.get("symbol") or "")
            requested_symbol = normalized_symbols.get(api_symbol)
            if requested_symbol is None:
                continue
            price = _optional_decimal(item.get("price"))
            if price is not None:
                prices[requested_symbol] = price
        return prices

    def get_historical_bars(
        self,
        symbol: str,
        timeframe: BarTimeframe,
        *,
        limit: int = 100,
    ) -> tuple[HistoricalBar, ...]:
        normalized_symbol = _binance_api_symbol(symbol)
        payload = _http_json_request(
            f"{_BINANCE_FUTURES_DEMO_BASE_URL}/fapi/v1/klines?{parse.urlencode({'symbol': normalized_symbol, 'interval': timeframe.value, 'limit': str(max(limit, 1))})}",
            method="GET",
            headers={"Accept": "application/json"},
            timeout_seconds=self.timeout_seconds,
        )
        return _binance_bars_to_domain(payload, _binance_display_symbol(normalized_symbol), timeframe)

    def list_closed_trades(self, *, limit: int = 100) -> tuple[NormalizedTrade, ...]:
        normalized_limit = max(1, min(limit, 500))
        fill_limit = max(50, min(1000, normalized_limit * 4))
        fills: list[NormalizedFill] = []
        for symbol in CRYPTO_SYMBOLS:
            payload = self._signed_request(
                "GET",
                "/fapi/v1/userTrades",
                {
                    "symbol": _binance_api_symbol(symbol),
                    "limit": str(fill_limit),
                },
            )
            if not isinstance(payload, list):
                continue
            for item in payload:
                if not isinstance(item, dict):
                    continue
                fills.append(_binance_trade_fill_to_domain(item))
        closed_trades = _closed_trades_from_fills(Market.CRYPTO, fills)
        return tuple(sorted(closed_trades, key=lambda trade: trade.closed_at, reverse=True)[:normalized_limit])

    def submit_order(self, order_request: OrderRequest) -> NormalizedOrder:
        if order_request.order_type != OrderType.MARKET:
            raise ValueError("Only market orders are supported for Binance Futures dashboard actions.")
        symbol = _binance_api_symbol(order_request.symbol)
        is_manual_close = order_request.client_order_id.startswith("close-")
        quantity = self._normalize_order_quantity(symbol, abs(order_request.quantity), order_request.limit_price)
        response = self._signed_request(
            "POST",
            "/fapi/v1/order",
            {
                "symbol": symbol,
                "side": order_request.side.value.upper(),
                "type": "MARKET",
                "quantity": _decimal_to_string(quantity),
                "newClientOrderId": order_request.client_order_id,
                "newOrderRespType": "RESULT",
                "reduceOnly": "true" if is_manual_close else "false",
            },
        )
        if not isinstance(response, dict):
            raise ValueError("Binance order response is invalid.")
        order = _binance_order_to_domain(response)
        self._order_symbols[order.order_id] = symbol
        return order

    def cancel_order(self, order_id: str) -> NormalizedOrder:
        symbol = self._order_symbols.get(order_id)
        if symbol is None:
            raise ValueError("Binance order symbol is unknown for cancellation.")
        response = self._signed_request(
            "DELETE",
            "/fapi/v1/order",
            {"symbol": symbol, "orderId": order_id},
        )
        if not isinstance(response, dict):
            raise ValueError("Binance cancel-order response is invalid.")
        order = _binance_order_to_domain(response)
        self._order_symbols[order.order_id] = symbol
        return order

    def get_order(self, order_id: str) -> NormalizedOrder | None:
        symbol = self._order_symbols.get(order_id)
        if symbol is None:
            return None
        response = self._signed_request(
            "GET",
            "/fapi/v1/order",
            {"symbol": symbol, "orderId": order_id},
        )
        if not isinstance(response, dict):
            raise ValueError("Binance get-order response is invalid.")
        order = _binance_order_to_domain(response)
        self._order_symbols[order.order_id] = symbol
        return order

    def reconcile(self, timeout_seconds: int | None = None) -> BrokerReconciliationResult:
        account = self.get_account()
        positions = tuple(self.list_positions())
        open_orders_payload = self._signed_request("GET", "/fapi/v1/openOrders", {})
        open_orders = tuple(
            _binance_order_to_domain(item)
            for item in open_orders_payload
            if isinstance(open_orders_payload, list) and isinstance(item, dict)
        ) if isinstance(open_orders_payload, list) else ()
        for order in open_orders:
            self._order_symbols[order.order_id] = _binance_api_symbol(order.symbol)
        return BrokerReconciliationResult(
            account=account,
            positions=positions,
            open_orders=open_orders,
            timeout_seconds=timeout_seconds or self.metadata().reconciliation_policy.default_timeout_seconds,
        )

    def _signed_request(self, method: str, path: str, params: dict[str, str]) -> object:
        return self._signed_request_with_retry(method, path, params, allow_time_retry=True)

    def _signed_request_with_retry(
        self,
        method: str,
        path: str,
        params: dict[str, str],
        *,
        allow_time_retry: bool,
    ) -> object:
        base_params = dict(params)
        base_params["timestamp"] = str(self._timestamp_ms())
        base_params["recvWindow"] = "10000"
        query = parse.urlencode(base_params)
        signature = hmac.new(self.api_secret.encode("utf-8"), query.encode("utf-8"), hashlib.sha256).hexdigest()
        url = f"{_BINANCE_FUTURES_DEMO_BASE_URL}{path}?{query}&signature={signature}"
        try:
            return _http_json_request(
                url,
                method=method,
                headers={"X-MBX-APIKEY": self.api_key, "Accept": "application/json"},
                timeout_seconds=self.timeout_seconds,
            )
        except ValueError as exc:
            if allow_time_retry and _binance_is_timing_error(str(exc)):
                self._server_time_offset_ms = self._fetch_server_time_offset_ms()
                return self._signed_request_with_retry(method, path, params, allow_time_retry=False)
            raise

    def _timestamp_ms(self) -> int:
        return int(datetime.now(tz=UTC).timestamp() * 1000) + self._server_time_offset_ms

    def _fetch_server_time_offset_ms(self) -> int:
        payload = _http_json_request(
            f"{_BINANCE_FUTURES_DEMO_BASE_URL}/fapi/v1/time",
            method="GET",
            headers={"Accept": "application/json"},
            timeout_seconds=self.timeout_seconds,
        )
        if not isinstance(payload, dict):
            return 0
        server_time = int(payload.get("serverTime") or 0)
        if server_time <= 0:
            return 0
        local_time = int(datetime.now(tz=UTC).timestamp() * 1000)
        return server_time - local_time

    def _normalize_order_quantity(
        self,
        symbol: str,
        requested_quantity: Decimal,
        reference_price: Decimal | None,
    ) -> Decimal:
        rules = self._symbol_rules_for(symbol)
        quantity = max(requested_quantity, rules.min_qty)
        if rules.step_size > _ZERO:
            step_count = (quantity / rules.step_size).to_integral_value(rounding=ROUND_DOWN)
            quantity = step_count * rules.step_size
        if quantity < rules.min_qty:
            quantity = rules.min_qty
        if reference_price is not None and reference_price > _ZERO and rules.min_notional > _ZERO:
            minimum_quantity = rules.min_notional / reference_price
            if rules.step_size > _ZERO:
                step_count = (minimum_quantity / rules.step_size).to_integral_value(rounding=ROUND_DOWN)
                if step_count * rules.step_size < minimum_quantity:
                    step_count += 1
                minimum_quantity = step_count * rules.step_size
            quantity = max(quantity, minimum_quantity, rules.min_qty)
        return quantity.normalize()

    def _symbol_rules_for(self, symbol: str) -> BinanceSymbolRules:
        cached = self._symbol_rules.get(symbol)
        if cached is not None:
            return cached

        payload = _http_json_request(
            f"{_BINANCE_FUTURES_DEMO_BASE_URL}/fapi/v1/exchangeInfo?symbol={symbol}",
            method="GET",
            headers={"Accept": "application/json"},
            timeout_seconds=self.timeout_seconds,
        )
        rules = _binance_symbol_rules(payload, symbol)
        self._symbol_rules[symbol] = rules
        return rules


@dataclass(slots=True)
class IgBrokerAdapter(BrokerAdapter):
    username: str
    password: str
    api_key: str
    demo_mode: bool = True
    timeout_seconds: int = 15
    _position_contexts: dict[str, dict[str, str]] = field(default_factory=dict)
    _order_cache: dict[str, NormalizedOrder] = field(default_factory=dict)
    _session_headers: MappingProxyType[str, str] | None = None
    _session_expires_at: datetime | None = None

    def metadata(self) -> BrokerMetadata:
        return BrokerMetadata(
            adapter_name="ig-forex-au",
            market=Market.FOREX,
            environment=BrokerEnvironment.SANDBOX if self.demo_mode else BrokerEnvironment.LIVE,
            capabilities=frozenset(
                {
                    BrokerCapability.SUBMIT_ORDER,
                    BrokerCapability.QUERY_ORDER,
                    BrokerCapability.RECONCILE,
                    BrokerCapability.HEALTH_CHECK,
                }
            ),
            safety_policy=BrokerSafetyPolicy(require_market_arming=True, allow_live_execution=False),
            reconciliation_policy=BrokerReconciliationPolicy(default_timeout_seconds=20, hard_timeout_seconds=40),
        )

    def health_check(self) -> BrokerHealth:
        try:
            account = self.get_account()
        except Exception as exc:
            return BrokerHealth(status=BrokerHealthStatus.UNHEALTHY, message=str(exc))
        return BrokerHealth(status=BrokerHealthStatus.HEALTHY, message=f"connected to IG account {account.account_id}")

    def get_account(self) -> NormalizedAccount:
        session_headers = self._create_session_headers()
        accounts_payload = self._request_json("GET", "/accounts", headers=session_headers)
        if not isinstance(accounts_payload, dict):
            raise ValueError("IG accounts payload is invalid.")
        accounts = accounts_payload.get("accounts")
        if not isinstance(accounts, list) or not accounts:
            raise ValueError("IG returned no account records.")
        preferred = next((item for item in accounts if isinstance(item, dict) and item.get("preferred")), accounts[0])
        if not isinstance(preferred, dict):
            raise ValueError("IG preferred account payload is invalid.")
        balance = _as_object_dict(preferred.get("balance"))
        return NormalizedAccount(
            account_id=str(preferred.get("accountId") or preferred.get("accountName") or "ig-account"),
            currency=str(preferred.get("currency") or "USD"),
            equity=_decimal(balance.get("balance")),
            buying_power=_decimal(balance.get("available")),
            cash=_decimal(balance.get("available")),
        )

    def list_positions(self) -> list[NormalizedPosition]:
        session_headers = self._create_session_headers()
        payload = self._request_json("GET", "/positions", headers=session_headers)
        if not isinstance(payload, dict):
            raise ValueError("IG positions payload is invalid.")
        positions_payload = payload.get("positions")
        if not isinstance(positions_payload, list):
            return []
        positions: list[NormalizedPosition] = []
        self._position_contexts.clear()
        for item in positions_payload:
            if not isinstance(item, dict):
                continue
            position = _as_object_dict(item.get("position"))
            market = _as_object_dict(item.get("market"))
            size = _decimal(position.get("size"))
            direction = str(position.get("direction") or "BUY").upper()
            if direction == "SELL":
                size = -size
            bid = _decimal(market.get("bid"))
            offer = _decimal(market.get("offer"))
            mid_price = (bid + offer) / Decimal("2") if bid or offer else _decimal(position.get("level"))
            symbol = _ig_symbol_from_market_payload(market)
            self._position_contexts[_normalize_forex_symbol(symbol)] = {
                "dealId": str(position.get("dealId") or ""),
                "epic": str(market.get("epic") or ""),
                "expiry": str(market.get("expiry") or "-") or "-",
                "symbol": symbol,
            }
            positions.append(
                NormalizedPosition(
                    symbol=symbol,
                    quantity=size,
                    average_price=_decimal(position.get("level")),
                    market_price=mid_price,
                )
            )
        return positions

    def get_latest_price(self, symbol: str) -> Decimal | None:
        epic = _ig_epic_for_symbol(symbol)
        if not epic:
            return None
        session_headers = self._create_session_headers()
        payload = self._request_json("GET", f"/markets/{epic}", headers=session_headers)
        return _ig_market_price(payload)

    def submit_order(self, order_request: OrderRequest) -> NormalizedOrder:
        if order_request.order_type != OrderType.MARKET:
            raise ValueError("Only market orders are supported for IG dashboard actions.")
        session_headers = self._create_session_headers()
        if order_request.client_order_id.startswith("close-"):
            position_context = self._position_contexts.get(_normalize_forex_symbol(order_request.symbol))
            if position_context is None:
                self.list_positions()
                position_context = self._position_contexts.get(_normalize_forex_symbol(order_request.symbol))
            if position_context is None or not position_context.get("dealId"):
                raise ValueError(f"No IG position context found for {order_request.symbol}.")
            response = self._request_json(
                "DELETE",
                "/positions/otc",
                payload={
                    "dealId": position_context["dealId"],
                    "direction": order_request.side.value.upper(),
                    "epic": position_context["epic"],
                    "expiry": position_context.get("expiry") or "-",
                    "size": _decimal_to_string(abs(order_request.quantity)),
                    "orderType": "MARKET",
                    "timeInForce": "FILL_OR_KILL",
                },
                headers=session_headers,
            )
            if not isinstance(response, dict):
                raise ValueError("IG close-position response is invalid.")
            fallback_symbol = position_context["symbol"]
        else:
            epic = _ig_epic_for_symbol(order_request.symbol)
            if not epic:
                raise ValueError(f"No IG epic mapping configured for {order_request.symbol}.")
            account = self.get_account()
            response = self._request_json(
                "POST",
                "/positions/otc",
                payload={
                    "currencyCode": account.currency or "AUD",
                    "direction": order_request.side.value.upper(),
                    "epic": epic,
                    "expiry": "-",
                    "forceOpen": False,
                    "guaranteedStop": False,
                    "orderType": "MARKET",
                    "size": _decimal_to_string(abs(order_request.quantity)),
                    "timeInForce": "FILL_OR_KILL",
                },
                headers=session_headers,
            )
            if not isinstance(response, dict):
                raise ValueError("IG open-position response is invalid.")
            fallback_symbol = _normalize_forex_symbol(order_request.symbol)
        deal_reference = str(response.get("dealReference") or "")
        if not deal_reference:
            raise ValueError("IG dealing response did not return a deal reference.")
        confirmation = self._request_json("GET", f"/confirms/{deal_reference}", headers=session_headers)
        order = _ig_confirmation_to_order(
            confirmation,
            client_order_id=order_request.client_order_id,
            fallback_symbol=fallback_symbol,
        )
        self._order_cache[order.order_id] = order
        return order

    def cancel_order(self, order_id: str) -> NormalizedOrder:
        raise ValueError("IG dashboard orders are executed immediately and cannot be canceled.")

    def get_order(self, order_id: str) -> NormalizedOrder | None:
        return self._order_cache.get(order_id)

    def reconcile(self, timeout_seconds: int | None = None) -> BrokerReconciliationResult:
        return BrokerReconciliationResult(
            account=self.get_account(),
            positions=tuple(self.list_positions()),
            open_orders=(),
            timeout_seconds=timeout_seconds or self.metadata().reconciliation_policy.default_timeout_seconds,
        )

    def _create_session_headers(self) -> MappingProxyType[str, str]:
        now = datetime.now(UTC)
        if self._session_headers is not None and self._session_expires_at is not None and now < self._session_expires_at:
            return self._session_headers
        payload = self._request_json(
            "POST",
            "/session",
            payload={"identifier": self.username, "password": self.password},
            headers={"X-IG-API-KEY": self.api_key, "Version": "2"},
            include_response_headers=True,
        )
        if not isinstance(payload, dict):
            raise ValueError("IG session payload is invalid.")
        headers = _as_object_dict(payload.get("headers"))
        cst = headers.get("CST")
        security_token = headers.get("X-SECURITY-TOKEN")
        if not isinstance(cst, str) or not cst or not isinstance(security_token, str) or not security_token:
            raise ValueError("IG session did not return CST and X-SECURITY-TOKEN headers.")
        self._session_headers = MappingProxyType({
            "X-IG-API-KEY": self.api_key,
            "CST": cst,
            "X-SECURITY-TOKEN": security_token,
            "Version": "1",
        })
        self._session_expires_at = now + timedelta(seconds=55)
        return self._session_headers

    def _request_json(
        self,
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
        headers: dict[str, str] | MappingProxyType[str, str] | None = None,
        include_response_headers: bool = False,
    ) -> object:
        base_url = "https://demo-api.ig.com/gateway/deal" if self.demo_mode else "https://api.ig.com/gateway/deal"
        response = _http_json_request(
            base_url + path,
            method=method,
            headers={"Accept": "application/json", **dict(headers or {})},
            payload=payload,
            timeout_seconds=self.timeout_seconds,
            include_response_headers=include_response_headers,
        )
        return response


def build_stocks_adapter(config: AppConfig, resolver: BrokerSecretResolver) -> BrokerAdapter:
    api_key = resolver.resolve(ALPACA_API_KEY_SECRET_ID)
    api_secret = resolver.resolve(ALPACA_API_SECRET_SECRET_ID)
    if not api_key or not api_secret:
        return UnconfiguredBrokerAdapter(
            market=Market.STOCKS,
            adapter_name="alpaca",
            environment=BrokerEnvironment.SANDBOX,
            reason="Alpaca credentials are not configured.",
        )
    return AlpacaBrokerAdapter(api_key=api_key, api_secret=api_secret, paper_trading=config.broker_paper_trading)


def build_crypto_adapter(resolver: BrokerSecretResolver) -> BrokerAdapter:
    api_key = resolver.resolve(BINANCE_API_KEY_SECRET_ID)
    api_secret = resolver.resolve(BINANCE_API_SECRET_SECRET_ID)
    if not api_key or not api_secret:
        return UnconfiguredBrokerAdapter(
            market=Market.CRYPTO,
            adapter_name="binance",
            environment=BrokerEnvironment.SANDBOX,
            reason="Binance Futures demo credentials are not configured.",
        )
    return BinanceBrokerAdapter(api_key=api_key, api_secret=api_secret)


def build_forex_adapter(resolver: BrokerSecretResolver) -> BrokerAdapter:
    username = resolver.resolve(IG_USERNAME_SECRET_ID)
    password = resolver.resolve(IG_PASSWORD_SECRET_ID)
    api_key = resolver.resolve(IG_API_KEY_SECRET_ID)
    if not username or not password or not api_key:
        return UnconfiguredBrokerAdapter(
            market=Market.FOREX,
            adapter_name="ig-forex-au",
            environment=BrokerEnvironment.SANDBOX,
            reason="IG Forex AU demo credentials are not configured.",
        )
    return IgBrokerAdapter(username=username, password=password, api_key=api_key, demo_mode=True)


def _http_json_request(
    url: str,
    *,
    method: str,
    headers: dict[str, str],
    payload: dict[str, object] | None = None,
    timeout_seconds: int = 15,
    include_response_headers: bool = False,
) -> object:
    data: bytes | None = None
    normalized_headers = dict(headers)
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        normalized_headers.setdefault("Content-Type", "application/json")
    req = request.Request(url, data=data, method=method, headers=normalized_headers)
    try:
        with request.urlopen(req, timeout=timeout_seconds) as response:
            raw_body = response.read().decode("utf-8")
            parsed_body = json.loads(raw_body) if raw_body else {}
            if include_response_headers:
                return {"body": parsed_body, "headers": dict(response.headers.items())}
            return parsed_body
    except error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise ValueError(f"HTTP {exc.code} from broker API: {error_body or exc.reason}") from exc
    except error.URLError as exc:
        raise ValueError(f"Broker API request failed: {exc.reason}") from exc


def _decimal(value: object) -> Decimal:
    try:
        if value is None or value == "":
            return _ZERO
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return _ZERO


def _decimal_to_string(value: Decimal) -> str:
    normalized = value.normalize()
    return format(normalized, "f") if normalized == normalized.to_integral() else format(value, "f")


def _alpaca_time_in_force(value: TimeInForce) -> str:
    if value == TimeInForce.GTC:
        return "gtc"
    if value == TimeInForce.IOC:
        return "ioc"
    return "day"


def _alpaca_order_to_domain(payload: object) -> NormalizedOrder:
    if not isinstance(payload, dict):
        raise ValueError("Alpaca order payload is invalid.")
    return NormalizedOrder(
        order_id=str(payload.get("id") or payload.get("order_id") or "alpaca-order"),
        client_order_id=str(payload.get("client_order_id") or ""),
        symbol=str(payload.get("symbol") or ""),
        side=OrderSide(str(payload.get("side") or OrderSide.BUY.value)),
        quantity=_decimal(payload.get("qty")),
        filled_quantity=_decimal(payload.get("filled_qty")),
        order_type=_alpaca_order_type(str(payload.get("type") or "market")),
        status=_alpaca_order_status(str(payload.get("status") or "accepted")),
        time_in_force=_alpaca_response_time_in_force(str(payload.get("time_in_force") or "day")),
        limit_price=_optional_decimal(payload.get("limit_price")),
        average_fill_price=_optional_decimal(payload.get("filled_avg_price")),
    )


def _alpaca_bar_timeframe(value: BarTimeframe) -> str:
    if value == BarTimeframe.ONE_MINUTE:
        return "1Min"
    if value == BarTimeframe.FIVE_MINUTES:
        return "5Min"
    if value == BarTimeframe.FIFTEEN_MINUTES:
        return "15Min"
    if value == BarTimeframe.ONE_HOUR:
        return "1Hour"
    return "1Day"


def _alpaca_bars_to_domain(
    payload: object,
    symbol: str,
    timeframe: BarTimeframe,
) -> tuple[HistoricalBar, ...]:
    if not isinstance(payload, dict):
        return ()
    bars_payload = _as_object_dict(payload.get("bars"))
    raw_bars = bars_payload.get(symbol)
    if not isinstance(raw_bars, list):
        return ()
    bars: list[HistoricalBar] = []
    for item in raw_bars:
        if not isinstance(item, dict):
            continue
        opened_at = _timestamp_from_value(item.get("t"))
        if opened_at is None:
            continue
        bars.append(
            HistoricalBar(
                market=Market.STOCKS,
                symbol=symbol,
                timeframe=timeframe,
                open_price=_decimal(item.get("o")),
                high_price=_decimal(item.get("h")),
                low_price=_decimal(item.get("l")),
                close_price=_decimal(item.get("c")),
                volume=_decimal(item.get("v")),
                opened_at=opened_at,
                closed_at=opened_at + _timeframe_delta(timeframe),
            )
        )
    return tuple(bars)


def _alpaca_order_type(raw_value: str) -> OrderType:
    normalized = raw_value.lower()
    if normalized == "limit":
        return OrderType.LIMIT
    if normalized == "stop":
        return OrderType.STOP
    return OrderType.MARKET


def _alpaca_order_status(raw_value: str) -> OrderStatus:
    normalized = raw_value.lower()
    if normalized in {"canceled", "cancelled", "expired"}:
        return OrderStatus.CANCELED
    if normalized in {"filled"}:
        return OrderStatus.FILLED
    if normalized in {"rejected"}:
        return OrderStatus.REJECTED
    if normalized in {"new", "accepted", "pending_new", "accepted_for_bidding"}:
        return OrderStatus.ACCEPTED
    return OrderStatus.OPEN


def _alpaca_response_time_in_force(raw_value: str) -> TimeInForce:
    normalized = raw_value.lower()
    if normalized == "gtc":
        return TimeInForce.GTC
    if normalized == "ioc":
        return TimeInForce.IOC
    return TimeInForce.DAY


def _optional_decimal(value: object) -> Decimal | None:
    if value is None or value == "":
        return None
    return _decimal(value)


def _as_object_dict(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    return cast(dict[str, object], value)


def _timestamp_from_value(value: object) -> datetime | None:
    if value in {None, ""}:
        return None
    raw_value = str(value)
    try:
        if raw_value.endswith("Z"):
            return datetime.fromisoformat(raw_value.replace("Z", "+00:00"))
        return datetime.fromisoformat(raw_value)
    except ValueError:
        return None


def _binance_futures_positions(payload: object) -> list[NormalizedPosition]:
    if not isinstance(payload, dict):
        return []
    positions_payload = payload.get("positions")
    if not isinstance(positions_payload, list):
        return []
    positions: list[NormalizedPosition] = []
    for item in positions_payload:
        if not isinstance(item, dict):
            continue
        quantity = _binance_position_quantity(item)
        if quantity == _ZERO:
            continue
        market_price = _optional_decimal(item.get("markPrice"))
        if market_price is None or market_price <= _ZERO:
            market_price = _binance_position_market_price(item, quantity)
        average_price = _optional_decimal(item.get("entryPrice"))
        if average_price is None or average_price <= _ZERO:
            average_price = _binance_position_entry_price(item, quantity, market_price)
        positions.append(
            NormalizedPosition(
                symbol=_binance_display_symbol(str(item.get("symbol") or "")),
                quantity=quantity,
                average_price=average_price or _ZERO,
                market_price=market_price or _ZERO,
            )
        )
    return positions


def _binance_position_market_price(item: dict[str, object], quantity: Decimal) -> Decimal | None:
    if quantity == _ZERO:
        return None
    notional = _optional_decimal(item.get("notional"))
    if notional is not None and notional != _ZERO:
        derived = abs(notional) / abs(quantity)
        if derived > _ZERO:
            return derived
    break_even = _optional_decimal(item.get("breakEvenPrice"))
    if break_even is not None and break_even > _ZERO:
        return break_even
    return None


def _binance_position_entry_price(
    item: dict[str, object],
    quantity: Decimal,
    market_price: Decimal | None,
) -> Decimal | None:
    break_even = _optional_decimal(item.get("breakEvenPrice"))
    if break_even is not None and break_even > _ZERO:
        return break_even
    unrealized_profit = _optional_decimal(item.get("unrealizedProfit"))
    if (
        unrealized_profit is not None
        and quantity != _ZERO
        and market_price is not None
        and market_price > _ZERO
    ):
        derived = market_price - (unrealized_profit / quantity)
        if derived > _ZERO:
            return derived
    return market_price if market_price is not None and market_price > _ZERO else None


def _binance_bars_to_domain(
    payload: object,
    symbol: str,
    timeframe: BarTimeframe,
) -> tuple[HistoricalBar, ...]:
    if not isinstance(payload, list):
        return ()
    bars: list[HistoricalBar] = []
    for item in payload:
        if not isinstance(item, list) or len(item) < 7:
            continue
        opened_at = _timestamp_ms_to_datetime(item[0])
        if opened_at is None:
            continue
        closed_at = _timestamp_ms_to_datetime(item[6])
        if closed_at is None:
            closed_at = opened_at + _timeframe_delta(timeframe)
        bars.append(
            HistoricalBar(
                market=Market.CRYPTO,
                symbol=symbol,
                timeframe=timeframe,
                open_price=_decimal(item[1]),
                high_price=_decimal(item[2]),
                low_price=_decimal(item[3]),
                close_price=_decimal(item[4]),
                volume=_decimal(item[5]),
                opened_at=opened_at,
                closed_at=closed_at,
            )
        )
    return tuple(bars)


def _binance_api_symbol(value: str) -> str:
    return value.upper().replace("/", "")


def _timestamp_ms_to_datetime(value: object) -> datetime | None:
    try:
        timestamp_ms = int(str(value))
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC)


def _timeframe_delta(value: BarTimeframe) -> timedelta:
    if value == BarTimeframe.ONE_MINUTE:
        return timedelta(minutes=1)
    if value == BarTimeframe.FIVE_MINUTES:
        return timedelta(minutes=5)
    if value == BarTimeframe.FIFTEEN_MINUTES:
        return timedelta(minutes=15)
    if value == BarTimeframe.ONE_HOUR:
        return timedelta(hours=1)
    return timedelta(days=1)


def _binance_display_symbol(value: str) -> str:
    normalized = _binance_api_symbol(value)
    for quote_asset in ("USDT", "USDC", "BUSD", "FDUSD"):
        if normalized.endswith(quote_asset) and len(normalized) > len(quote_asset):
            return f"{normalized[:-len(quote_asset)]}/{quote_asset}"
    return normalized


def _binance_position_quantity(payload: dict[str, object]) -> Decimal:
    quantity = _decimal(payload.get("positionAmt"))
    position_side = str(payload.get("positionSide") or "BOTH").upper()
    if quantity == _ZERO:
        return _ZERO
    if position_side == "SHORT" and quantity > _ZERO:
        return -quantity
    if position_side == "LONG" and quantity < _ZERO:
        return abs(quantity)
    return quantity


def _binance_symbol_rules(payload: object, symbol: str) -> BinanceSymbolRules:
    if not isinstance(payload, dict):
        return BinanceSymbolRules(min_qty=Decimal("0.001"), step_size=Decimal("0.001"))
    symbols = payload.get("symbols")
    if not isinstance(symbols, list):
        return BinanceSymbolRules(min_qty=Decimal("0.001"), step_size=Decimal("0.001"))
    raw_symbol = next(
        (
            item
            for item in symbols
            if isinstance(item, dict) and str(item.get("symbol") or "").upper() == symbol.upper()
        ),
        None,
    )
    if not isinstance(raw_symbol, dict):
        return BinanceSymbolRules(min_qty=Decimal("0.001"), step_size=Decimal("0.001"))

    min_qty = Decimal("0.001")
    step_size = Decimal("0.001")
    min_notional = _ZERO
    filters = raw_symbol.get("filters")
    if isinstance(filters, list):
        for item in filters:
            if not isinstance(item, dict):
                continue
            filter_type = str(item.get("filterType") or "")
            if filter_type in {"LOT_SIZE", "MARKET_LOT_SIZE"}:
                min_qty = _optional_decimal(item.get("minQty")) or min_qty
                step_size = _optional_decimal(item.get("stepSize")) or step_size
            if filter_type in {"MIN_NOTIONAL", "NOTIONAL"}:
                min_notional = _optional_decimal(item.get("notional") or item.get("minNotional")) or min_notional
    return BinanceSymbolRules(min_qty=min_qty, step_size=step_size, min_notional=min_notional)


def _binance_is_timing_error(message: str) -> bool:
    normalized = (message or "").lower()
    return (
        "timestamp for this request" in normalized
        or "recvwindow" in normalized
        or "server time" in normalized
        or '"code":-1021' in normalized
    )


def _binance_order_to_domain(payload: dict[str, object]) -> NormalizedOrder:
    side = str(payload.get("side") or OrderSide.BUY.value).lower()
    status = str(payload.get("status") or "NEW").lower()
    time_in_force = str(payload.get("timeInForce") or "GTC").lower()
    order_type = str(payload.get("type") or "MARKET").lower()
    executed_quantity = _decimal(payload.get("executedQty") or payload.get("cumQty"))
    cumulative_quote_quantity = _decimal(payload.get("cummulativeQuoteQty") or payload.get("cumQuote"))
    average_fill_price = _optional_decimal(payload.get("avgPrice"))
    if average_fill_price in {None, _ZERO} and executed_quantity > _ZERO:
        average_fill_price = cumulative_quote_quantity / executed_quantity
    return NormalizedOrder(
        order_id=str(payload.get("orderId") or ""),
        client_order_id=str(payload.get("clientOrderId") or ""),
        symbol=_binance_display_symbol(str(payload.get("symbol") or "")),
        side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
        quantity=_decimal(payload.get("origQty")),
        filled_quantity=executed_quantity,
        order_type=OrderType.LIMIT if order_type == "limit" else OrderType.STOP if "stop" in order_type else OrderType.MARKET,
        status=OrderStatus.OPEN if status in {"new", "partially_filled"} else OrderStatus.FILLED if status == "filled" else OrderStatus.CANCELED if status in {"canceled", "expired"} else OrderStatus.REJECTED if status == "rejected" else OrderStatus.ACCEPTED,
        time_in_force=TimeInForce.GTC if time_in_force == "gtc" else TimeInForce.IOC if time_in_force == "ioc" else TimeInForce.DAY,
        limit_price=_optional_decimal(payload.get("price")),
        average_fill_price=None if average_fill_price == _ZERO else average_fill_price,
    )


@dataclass(slots=True)
class _OpenFillLot:
    fill_id: str
    order_id: str
    symbol: str
    side: OrderSide
    quantity: Decimal
    price: Decimal
    executed_at: datetime
    fees: Decimal


def _binance_trade_fill_to_domain(payload: dict[str, object]) -> NormalizedFill:
    raw_side = str(payload.get("side") or "").upper()
    if raw_side == "BUY":
        side = OrderSide.BUY
    elif raw_side == "SELL":
        side = OrderSide.SELL
    else:
        side = OrderSide.BUY if bool(payload.get("buyer")) else OrderSide.SELL
    executed_at = _timestamp_ms_to_datetime(payload.get("time")) or datetime.fromtimestamp(0, tz=UTC)
    return NormalizedFill(
        fill_id=str(payload.get("id") or payload.get("tradeId") or payload.get("time") or "fill"),
        order_id=str(payload.get("orderId") or payload.get("id") or "order"),
        client_order_id=str(payload.get("clientOrderId") or payload.get("orderId") or ""),
        symbol=_binance_display_symbol(str(payload.get("symbol") or "")),
        side=side,
        quantity=_decimal(payload.get("qty") or payload.get("executedQty") or payload.get("origQty")),
        price=_decimal(payload.get("price")),
        commission=_decimal(payload.get("commission")),
        executed_at=executed_at,
    )


def _closed_trades_from_fills(
    market: Market,
    fills: list[NormalizedFill],
) -> tuple[NormalizedTrade, ...]:
    if not fills:
        return ()

    trades: list[NormalizedTrade] = []
    lots_by_symbol: dict[str, list[_OpenFillLot]] = {}

    for fill in sorted(fills, key=lambda item: (item.executed_at, item.fill_id)):
        if fill.quantity <= _ZERO or fill.price <= _ZERO or not fill.symbol:
            continue

        symbol_lots = lots_by_symbol.setdefault(fill.symbol, [])
        remaining_quantity = fill.quantity
        remaining_close_fee = fill.commission
        trade_index = 0

        while remaining_quantity > _ZERO and symbol_lots and symbol_lots[0].side != fill.side:
            lot = symbol_lots[0]
            matched_quantity = min(lot.quantity, remaining_quantity)
            if matched_quantity <= _ZERO:
                symbol_lots.pop(0)
                continue

            opening_fee_share = _allocate_proportional_fee(lot.fees, matched_quantity, lot.quantity)
            closing_fee_share = _allocate_proportional_fee(
                remaining_close_fee,
                matched_quantity,
                remaining_quantity,
            )
            trades.append(
                NormalizedTrade(
                    trade_id=(
                        f"{market.value}-{fill.symbol.replace('/', '').lower()}-"
                        f"{lot.fill_id}-{fill.fill_id}-{trade_index}"
                    ),
                    market=market,
                    symbol=fill.symbol,
                    side=lot.side,
                    quantity=matched_quantity,
                    entry_price=lot.price,
                    exit_price=fill.price,
                    opened_at=lot.executed_at,
                    closed_at=fill.executed_at,
                    fees=opening_fee_share + closing_fee_share,
                )
            )
            trade_index += 1

            lot.quantity -= matched_quantity
            lot.fees -= opening_fee_share
            remaining_quantity -= matched_quantity
            remaining_close_fee -= closing_fee_share

            if lot.quantity <= _ZERO:
                symbol_lots.pop(0)

        if remaining_quantity > _ZERO:
            symbol_lots.append(
                _OpenFillLot(
                    fill_id=fill.fill_id,
                    order_id=fill.order_id,
                    symbol=fill.symbol,
                    side=fill.side,
                    quantity=remaining_quantity,
                    price=fill.price,
                    executed_at=fill.executed_at,
                    fees=remaining_close_fee,
                )
            )

    return tuple(trades)


def _allocate_proportional_fee(total_fee: Decimal, matched_quantity: Decimal, base_quantity: Decimal) -> Decimal:
    if total_fee <= _ZERO or matched_quantity <= _ZERO or base_quantity <= _ZERO:
        return _ZERO
    return total_fee * (matched_quantity / base_quantity)


def _ig_confirmation_to_order(
    payload: object,
    *,
    client_order_id: str,
    fallback_symbol: str,
) -> NormalizedOrder:
    if not isinstance(payload, dict):
        raise ValueError("IG confirmation payload is invalid.")
    raw_status = str(payload.get("dealStatus") or payload.get("status") or "ACCEPTED").upper()
    status = OrderStatus.FILLED if raw_status == "ACCEPTED" else OrderStatus.REJECTED
    level = _optional_decimal(payload.get("level"))
    quantity = _decimal(payload.get("size"))
    direction = str(payload.get("direction") or "BUY").lower()
    return NormalizedOrder(
        order_id=str(payload.get("dealId") or payload.get("dealReference") or client_order_id),
        client_order_id=client_order_id,
        symbol=_ig_symbol_from_epic(str(payload.get("epic") or "")) or fallback_symbol,
        side=OrderSide.BUY if direction == "buy" else OrderSide.SELL,
        quantity=quantity,
        filled_quantity=quantity if status == OrderStatus.FILLED else _ZERO,
        order_type=OrderType.MARKET,
        status=status,
        time_in_force=TimeInForce.IOC,
        average_fill_price=level,
    )


def _ig_market_price(payload: object) -> Decimal | None:
    if not isinstance(payload, dict):
        return None
    snapshot = _as_object_dict(payload.get("snapshot"))
    bid = _optional_decimal(snapshot.get("bid"))
    offer = _optional_decimal(snapshot.get("offer"))
    if bid is not None and offer is not None:
        return (bid + offer) / Decimal("2")
    return bid or offer


def _alpaca_snapshot_price(payload: object) -> Decimal | None:
    if not isinstance(payload, dict):
        return None
    latest_trade = _as_object_dict(payload.get("latestTrade"))
    trade_price = _optional_decimal(latest_trade.get("p"))
    if trade_price is not None:
        return trade_price
    latest_quote = _as_object_dict(payload.get("latestQuote"))
    bid = _optional_decimal(latest_quote.get("bp"))
    ask = _optional_decimal(latest_quote.get("ap"))
    if bid is not None and ask is not None:
        return (bid + ask) / Decimal("2")
    return bid or ask


def _ig_epic_for_symbol(symbol: str) -> str | None:
    return _IG_FOREX_EPICS.get(_normalize_forex_symbol(symbol))


def _ig_symbol_from_epic(epic: str) -> str | None:
    normalized = str(epic or "").strip().upper()
    for symbol, mapped_epic in _IG_FOREX_EPICS.items():
        if mapped_epic.upper() == normalized:
            return symbol
    return None


def _ig_symbol_from_market_payload(market: dict[str, object]) -> str:
    epic = str(market.get("epic") or "")
    instrument_name = str(market.get("instrumentName") or "")
    return _ig_symbol_from_epic(epic) or _normalize_forex_symbol(instrument_name) or "FOREX"


def _normalize_forex_symbol(symbol: str) -> str:
    return "".join(character for character in str(symbol or "").upper() if character.isalpha())