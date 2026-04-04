"""Shared runtime helpers for the live strategy scanner."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_DOWN, Decimal

from omnibot_v3.domain import Market, PortfolioSnapshot, RiskPolicy, StrategyProfile
from omnibot_v3.domain.broker import OrderRequest, OrderSide
from omnibot_v3.services.risk_engine import RiskPolicyEngine
from omnibot_v3.services.rolling_decision_support import (
    estimated_round_trip_cost_ratio,
    profile_settings,
)


@dataclass(frozen=True, slots=True)
class PortfolioControlDecision:
    accepted: bool
    reason: str = "accepted"


@dataclass(frozen=True, slots=True)
class ExecutionQualityDecision:
    accepted: bool
    reason: str = "accepted"


def portfolio_control_settings(profile_id: str) -> dict[str, Decimal | int]:
    settings: dict[str, dict[str, Decimal | int]] = {
        "conservative": {
            "max_open_positions": 1,
            "max_gross_exposure_ratio": Decimal("0.75"),
            "max_daily_loss_ratio": Decimal("0.010"),
            "max_correlated_positions": 0,
        },
        "moderate": {
            "max_open_positions": 2,
            "max_gross_exposure_ratio": Decimal("0.80"),
            "max_daily_loss_ratio": Decimal("0.015"),
            "max_correlated_positions": 1,
        },
        "aggressive": {
            "max_open_positions": 4,
            "max_gross_exposure_ratio": Decimal("1.50"),
            "max_daily_loss_ratio": Decimal("0.025"),
            "max_correlated_positions": 2,
        },
        "hft": {
            "max_open_positions": 6,
            "max_gross_exposure_ratio": Decimal("1.00"),
            "max_daily_loss_ratio": Decimal("0.012"),
            "max_correlated_positions": 3,
        },
    }
    return settings.get(profile_id, settings["moderate"])


def build_risk_engine(market: Market, profile_id: str) -> RiskPolicyEngine:
    del market
    max_order_notional = profile_settings(profile_id)["target_notional"]
    return RiskPolicyEngine(
        policy=RiskPolicy(
            max_order_notional=max_order_notional,
            max_position_notional=max_order_notional * Decimal("3"),
            max_daily_loss=max_order_notional,
            max_drawdown=max_order_notional * Decimal("2"),
        )
    )


def build_strategy_profile(market: Market, strategy_id: str, profile_id: str) -> StrategyProfile:
    return StrategyProfile(
        strategy_id=strategy_id,
        name=strategy_id.replace("-", " ").title(),
        version="scan-v2-7-aligned",
        market=market,
        description=f"{strategy_id} using profile {profile_id}",
        tags=(profile_id,),
        enabled=True,
    )


def order_quantity(market: Market, profile_id: str, price: Decimal) -> Decimal:
    if price <= Decimal("0"):
        return Decimal("0")
    target_notional = profile_settings(profile_id)["target_notional"]
    if market == Market.STOCKS:
        return max(Decimal("1"), (target_notional / price).to_integral_value(rounding=ROUND_DOWN))
    if market == Market.CRYPTO:
        return max(Decimal("0.001"), (target_notional / price).quantize(Decimal("0.001"), rounding=ROUND_DOWN))
    return {
        "conservative": Decimal("0.5"),
        "moderate": Decimal("0.75"),
        "aggressive": Decimal("1.25"),
        "hft": Decimal("0.5"),
    }.get(profile_id, Decimal("0.5"))


def execution_mode_for_profile(profile_id: str) -> str:
    del profile_id
    return "scan-and-trade"


def in_symbol_cooldown(last_trade_at: datetime | None, profile_id: str) -> bool:
    if last_trade_at is None:
        return False
    return (datetime.now(UTC) - last_trade_at).total_seconds() < profile_settings(profile_id)["cooldown_seconds"]


def evaluate_portfolio_controls(
    snapshot: PortfolioSnapshot,
    order_request: OrderRequest,
    profile_id: str,
    observed_at: datetime,
) -> PortfolioControlDecision:
    settings = portfolio_control_settings(profile_id)
    normalized_symbol = order_request.symbol.upper()
    matching_position = next(
        (position for position in snapshot.positions if position.symbol.upper() == normalized_symbol),
        None,
    )
    reduces_exposure = _reduces_exposure(matching_position, order_request)
    if not reduces_exposure and matching_position is None and len(snapshot.positions) >= int(settings["max_open_positions"]):
        return PortfolioControlDecision(accepted=False, reason="portfolio concurrency limit reached")

    reference_price = order_request.limit_price
    if reference_price is None and matching_position is not None:
        reference_price = matching_position.market_price
    order_notional = order_request.quantity * (reference_price or Decimal("0"))
    gross_exposure = sum((abs(position.market_value) for position in snapshot.positions), Decimal("0"))
    exposure_limit = snapshot.account.equity * Decimal(settings["max_gross_exposure_ratio"])
    if not reduces_exposure and gross_exposure + order_notional > exposure_limit:
        return PortfolioControlDecision(accepted=False, reason="portfolio exposure limit reached")

    if not reduces_exposure:
        correlated_exposures = _correlated_positions(snapshot, order_request)
        if correlated_exposures and len(correlated_exposures) >= int(settings["max_correlated_positions"]):
            dominant_factor = correlated_exposures[0][1]
            return PortfolioControlDecision(
                accepted=False,
                reason=f"correlated exposure limit reached ({dominant_factor})",
            )

    today_realized_pnl = sum(
        (
            trade.realized_pnl
            for trade in snapshot.closed_trades
            if trade.closed_at.astimezone(UTC).date() == observed_at.astimezone(UTC).date()
        ),
        Decimal("0"),
    )
    daily_loss_limit = snapshot.account.equity * Decimal(settings["max_daily_loss_ratio"])
    if today_realized_pnl <= -daily_loss_limit:
        return PortfolioControlDecision(accepted=False, reason="portfolio drawdown brake active")

    return PortfolioControlDecision(accepted=True)


def evaluate_execution_quality(
    *,
    market: Market,
    order_request: OrderRequest,
    fresh_price: Decimal | None,
    profile_id: str,
) -> ExecutionQualityDecision:
    if fresh_price is None or fresh_price <= Decimal("0"):
        return ExecutionQualityDecision(accepted=False, reason="fresh quote unavailable before execution")

    reference_price = order_request.limit_price
    if reference_price is None or reference_price <= Decimal("0"):
        return ExecutionQualityDecision(accepted=True)

    adverse_move_ratio = _adverse_execution_move_ratio(market, profile_id)
    if order_request.side == OrderSide.BUY:
        maximum_buy_price = reference_price * (Decimal("1") + adverse_move_ratio)
        if fresh_price > maximum_buy_price:
            return ExecutionQualityDecision(
                accepted=False,
                reason=(
                    f"execution quality degraded: buy price moved from {reference_price} to {fresh_price} "
                    f"beyond {adverse_move_ratio:.4%} tolerance"
                ),
            )
        return ExecutionQualityDecision(accepted=True)

    minimum_sell_price = reference_price * (Decimal("1") - adverse_move_ratio)
    if fresh_price < minimum_sell_price:
        return ExecutionQualityDecision(
            accepted=False,
            reason=(
                f"execution quality degraded: sell price moved from {reference_price} to {fresh_price} "
                f"beyond {adverse_move_ratio:.4%} tolerance"
            ),
        )
    return ExecutionQualityDecision(accepted=True)


def _adverse_execution_move_ratio(market: Market, profile_id: str) -> Decimal:
    profile = profile_settings(profile_id)
    cost_floor = estimated_round_trip_cost_ratio(market) * Decimal("2")
    profit_share_tolerance = profile["min_net_profit_ratio"] * Decimal("0.60")
    return max(cost_floor, profit_share_tolerance)


def _reduces_exposure(position, order_request: OrderRequest) -> bool:
    if position is None:
        return False
    if position.quantity > 0 and order_request.side == OrderSide.SELL:
        return True
    if position.quantity < 0 and order_request.side == OrderSide.BUY:
        return True
    return False


def _correlated_positions(
    snapshot: PortfolioSnapshot,
    order_request: OrderRequest,
) -> list[tuple[str, str]]:
    requested_factors = _correlation_factors_for_order(snapshot.market, order_request)
    if not requested_factors:
        return []

    correlated: list[tuple[str, str]] = []
    for position in snapshot.positions:
        position_factors = _correlation_factors_for_position(snapshot.market, position)
        shared = sorted(set(requested_factors).intersection(position_factors))
        if shared:
            correlated.append((position.symbol.upper(), shared[0]))
    return correlated


def _correlation_factors_for_order(market: Market, order_request: OrderRequest) -> tuple[str, ...]:
    direction = 1 if order_request.side == OrderSide.BUY else -1
    return _correlation_factors(market, order_request.symbol, direction)


def _correlation_factors_for_position(market: Market, position) -> tuple[str, ...]:
    direction = 1 if position.quantity >= Decimal("0") else -1
    return _correlation_factors(market, position.symbol, direction)


def _correlation_factors(market: Market, symbol: str, direction: int) -> tuple[str, ...]:
    normalized_symbol = symbol.strip().upper()
    direction_label = "long" if direction >= 0 else "short"
    if market == Market.STOCKS:
        stock_cluster = _stock_cluster(normalized_symbol)
        return (f"stocks:{stock_cluster}:{direction_label}",)
    if market == Market.CRYPTO:
        crypto_cluster = _crypto_cluster(normalized_symbol)
        return (f"crypto:{crypto_cluster}:{direction_label}",)
    if market == Market.FOREX:
        return _forex_factors(normalized_symbol, direction)
    return (f"{market.value}:{normalized_symbol}:{direction_label}",)


def _stock_cluster(symbol: str) -> str:
    if symbol in {"SPY", "QQQ", "IWM"}:
        return "index-beta"
    if symbol in {"AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "AMD", "AVGO", "PLTR", "SMCI", "NFLX", "TSLA"}:
        return "tech-growth"
    return symbol.lower()


def _crypto_cluster(symbol: str) -> str:
    base_asset = symbol.split("/", 1)[0].split("-", 1)[0]
    if base_asset in {"BTC", "ETH"}:
        return "majors"
    if base_asset in {"SOL", "BNB", "XRP", "ADA", "DOGE", "LINK", "AVAX", "LTC", "BCH", "SUI"}:
        return "alt-beta"
    return base_asset.lower()


def _forex_factors(symbol: str, direction: int) -> tuple[str, ...]:
    normalized_symbol = symbol.replace("/", "").replace("-", "")
    if len(normalized_symbol) < 6:
        direction_label = "long" if direction >= 0 else "short"
        return (f"forex:{normalized_symbol.lower()}:{direction_label}",)
    base_currency = normalized_symbol[:3]
    quote_currency = normalized_symbol[3:6]
    if direction >= 0:
        return (
            f"forex:{base_currency.lower()}-long",
            f"forex:{quote_currency.lower()}-short",
        )
    return (
        f"forex:{base_currency.lower()}-short",
        f"forex:{quote_currency.lower()}-long",
    )