"""Shared market baskets and selection normalization for trading modules."""

from __future__ import annotations

from omnibot_v3.domain.runtime import Market

STOCK_SYMBOLS: tuple[str, ...] = (
    "SPY",
    "QQQ",
    "AAPL",
    "MSFT",
    "NVDA",
    "AMZN",
    "META",
    "GOOGL",
    "AMD",
    "AVGO",
    "TSLA",
    "NFLX",
    "PLTR",
    "IWM",
    "SMCI",
)

CRYPTO_SYMBOLS: tuple[str, ...] = (
    "BTC/USDT",
    "ETH/USDT",
    "SOL/USDT",
    "BNB/USDT",
    "XRP/USDT",
    "ADA/USDT",
    "DOGE/USDT",
    "LINK/USDT",
    "AVAX/USDT",
    "LTC/USDT",
    "BCH/USDT",
    "SUI/USDT",
)

FOREX_SYMBOLS: tuple[str, ...] = (
    "EURUSD",
    "GBPUSD",
    "USDJPY",
    "AUDUSD",
    "USDCHF",
    "USDCAD",
    "NZDUSD",
    "EURGBP",
    "EURJPY",
    "GBPJPY",
    "EURCHF",
    "AUDJPY",
)

_LEGACY_STRATEGY_IDS: dict[Market, dict[str, str]] = {
    Market.STOCKS: {
        "equity-momentum": "momentum",
        "opening-breakout": "breakout",
        "mean-reversion": "mean_reversion",
    },
    Market.CRYPTO: {
        "trend-rider": "momentum",
        "breakout-volatility": "breakout",
        "reclaim-retest": "ml_ensemble",
    },
    Market.FOREX: {
        "macro-trend": "momentum",
        "london-breakout": "breakout",
        "asian-range": "mean_reversion",
    },
}

_LEGACY_PROFILE_IDS: dict[Market, dict[str, str]] = {
    Market.STOCKS: {
        "balanced-core": "moderate",
        "fast-tape": "aggressive",
        "defensive": "conservative",
    },
    Market.CRYPTO: {
        "weekend-liquidity": "moderate",
        "beta-seeking": "aggressive",
        "capital-preservation": "conservative",
    },
    Market.FOREX: {
        "low-drawdown": "conservative",
        "news-aware": "conservative",
        "carry-bias": "aggressive",
    },
}


def normalize_strategy_id(market: Market, strategy_id: str) -> str:
    normalized = str(strategy_id or "").strip().lower()
    if not normalized:
        return normalized
    return _LEGACY_STRATEGY_IDS.get(market, {}).get(normalized, normalized)


def normalize_profile_id(market: Market, profile_id: str) -> str:
    normalized = str(profile_id or "").strip().lower()
    if not normalized:
        return normalized
    return _LEGACY_PROFILE_IDS.get(market, {}).get(normalized, normalized)