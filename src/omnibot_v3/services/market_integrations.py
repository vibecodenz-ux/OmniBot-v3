"""Concrete market worker modules built on the broker adapter contract layer."""

from __future__ import annotations

from dataclasses import dataclass

from omnibot_v3.domain.broker import BrokerEnvironment
from omnibot_v3.domain.config import AppConfig
from omnibot_v3.domain.runtime import Market
from omnibot_v3.domain.worker import MarketWorkerSettings, MarketWorkerValidationResult
from omnibot_v3.infra.live_broker import build_live_market_workers
from omnibot_v3.infra.mock_broker import MockBrokerAdapter
from omnibot_v3.services.market_catalog import CRYPTO_SYMBOLS, FOREX_SYMBOLS, STOCK_SYMBOLS
from omnibot_v3.services.market_worker import MarketWorker
from omnibot_v3.services.secret_api import SecretRegistry
from omnibot_v3.services.secrets import SecretStoreService


def _normalize_symbols(symbols: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(symbol.strip().upper() for symbol in symbols)


@dataclass(slots=True)
class StocksWorker(MarketWorker):
    def validate_configuration(self) -> MarketWorkerValidationResult:
        result = MarketWorker.validate_configuration(self)
        symbols = _normalize_symbols(self.settings.symbols)
        extra_errors = [
            f"invalid stock symbol: {symbol}"
            for symbol in symbols
            if not symbol.isalnum() or len(symbol) > 5
        ]
        return self._merge_validation_result(result, extra_errors)

    @classmethod
    def build_default(cls) -> StocksWorker:
        settings = MarketWorkerSettings(
            market=Market.STOCKS,
            environment=BrokerEnvironment.SANDBOX,
            symbols=STOCK_SYMBOLS,
            poll_interval_seconds=5,
        )
        return cls(settings=settings, adapter=MockBrokerAdapter(market=Market.STOCKS))


@dataclass(slots=True)
class CryptoWorker(MarketWorker):
    def validate_configuration(self) -> MarketWorkerValidationResult:
        result = MarketWorker.validate_configuration(self)
        symbols = _normalize_symbols(self.settings.symbols)
        extra_errors = [
            f"invalid crypto symbol: {symbol}"
            for symbol in symbols
            if "/" not in symbol or len(symbol.split("/")) != 2 or not all(symbol.split("/"))
        ]
        return self._merge_validation_result(result, extra_errors)

    @classmethod
    def build_default(cls) -> CryptoWorker:
        settings = MarketWorkerSettings(
            market=Market.CRYPTO,
            environment=BrokerEnvironment.SANDBOX,
            symbols=CRYPTO_SYMBOLS,
            poll_interval_seconds=3,
        )
        return cls(settings=settings, adapter=MockBrokerAdapter(market=Market.CRYPTO))


@dataclass(slots=True)
class ForexWorker(MarketWorker):
    def validate_configuration(self) -> MarketWorkerValidationResult:
        result = MarketWorker.validate_configuration(self)
        symbols = _normalize_symbols(self.settings.symbols)
        extra_errors = [
            f"invalid forex symbol: {symbol}"
            for symbol in symbols
            if len(symbol) != 6 or not symbol.isalpha()
        ]
        return self._merge_validation_result(result, extra_errors)

    @classmethod
    def build_default(cls) -> ForexWorker:
        settings = MarketWorkerSettings(
            market=Market.FOREX,
            environment=BrokerEnvironment.SANDBOX,
            symbols=FOREX_SYMBOLS,
            poll_interval_seconds=2,
        )
        return cls(settings=settings, adapter=MockBrokerAdapter(market=Market.FOREX))


def build_default_market_workers() -> dict[Market, MarketWorker]:
    return {
        Market.STOCKS: StocksWorker.build_default(),
        Market.CRYPTO: CryptoWorker.build_default(),
        Market.FOREX: ForexWorker.build_default(),
    }


def build_configured_market_workers(
    config: AppConfig,
    registry: SecretRegistry,
    store_service: SecretStoreService,
) -> dict[Market, MarketWorker]:
    workers = build_live_market_workers(config=config, registry=registry, store_service=store_service)
    return {
        Market.STOCKS: workers[Market.STOCKS],
        Market.CRYPTO: workers[Market.CRYPTO],
        Market.FOREX: workers[Market.FOREX],
    }
