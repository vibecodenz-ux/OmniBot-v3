"""Learning telemetry and bounded score adjustments for the autonomous scanner."""

from __future__ import annotations

from collections.abc import Hashable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import TypedDict, TypeVar

from omnibot_v3.domain import Market, NormalizedTrade

_KeyT = TypeVar("_KeyT", bound=Hashable)


class _StrategyEdgePayload(TypedDict):
    market: str
    strategy_id: str
    closed_trades: int
    win_rate: str
    average_return_ratio: str
    average_score: str | None


class _ExecutionRiskPayload(TypedDict):
    market: str
    symbol: str
    strategy_id: str
    attempts: int
    orders_submitted: int
    execution_blocks: int
    order_rejections: int
    failure_rate: str
    average_slippage_ratio: str


@dataclass(frozen=True, slots=True)
class LearningAdjustment:
    score_delta: Decimal = Decimal("0")
    details: tuple[str, ...] = ()


@dataclass(slots=True)
class _OutcomeStats:
    closed_trades: int = 0
    wins: int = 0
    realized_return_total: Decimal = Decimal("0")
    score_total: Decimal = Decimal("0")
    score_count: int = 0


@dataclass(slots=True)
class _ExecutionStats:
    attempts: int = 0
    orders_submitted: int = 0
    execution_blocks: int = 0
    order_rejections: int = 0
    slippage_ratio_total: Decimal = Decimal("0")
    slippage_ratio_count: int = 0


@dataclass(slots=True)
class ScannerLearningState:
    _symbol_outcomes: dict[tuple[Market, str, str], _OutcomeStats] = field(default_factory=dict, init=False)
    _strategy_outcomes: dict[tuple[Market, str], _OutcomeStats] = field(default_factory=dict, init=False)
    _regime_outcomes: dict[tuple[Market, str, str], _OutcomeStats] = field(default_factory=dict, init=False)
    _symbol_execution: dict[tuple[Market, str, str], _ExecutionStats] = field(default_factory=dict, init=False)
    _strategy_execution: dict[tuple[Market, str], _ExecutionStats] = field(default_factory=dict, init=False)
    _processed_trade_ids: set[tuple[Market, str]] = field(default_factory=set, init=False)

    def score_adjustment(
        self,
        *,
        market: Market,
        symbol: str,
        strategy_id: str,
        regime: str | None,
    ) -> LearningAdjustment:
        normalized_symbol = symbol.strip().upper()
        normalized_strategy = strategy_id.strip().lower()
        normalized_regime = regime.strip().lower() if isinstance(regime, str) and regime.strip() else None

        symbol_outcome = self._symbol_outcomes.get((market, normalized_symbol, normalized_strategy))
        strategy_outcome = self._strategy_outcomes.get((market, normalized_strategy))
        regime_outcome = (
            self._regime_outcomes.get((market, normalized_strategy, normalized_regime))
            if normalized_regime is not None
            else None
        )
        symbol_execution = self._symbol_execution.get((market, normalized_symbol, normalized_strategy))
        strategy_execution = self._strategy_execution.get((market, normalized_strategy))

        outcome_delta = _weighted_blend(
            (
                (_outcome_score(symbol_outcome), Decimal("0.55")),
                (_outcome_score(strategy_outcome), Decimal("0.30")),
                (_outcome_score(regime_outcome), Decimal("0.15")),
            )
        )
        execution_penalty = _weighted_blend(
            (
                (_execution_penalty(symbol_execution), Decimal("0.65")),
                (_execution_penalty(strategy_execution), Decimal("0.35")),
            )
        )
        score_delta = max(Decimal("-1.25"), min(Decimal("1.25"), outcome_delta - execution_penalty))

        details: list[str] = []
        if outcome_delta != Decimal("0"):
            details.append(f"learning_outcome_delta={outcome_delta:.4f}")
        if execution_penalty != Decimal("0"):
            details.append(f"learning_execution_penalty={execution_penalty:.4f}")
        if score_delta != Decimal("0"):
            details.append(f"learning_score_delta={score_delta:.4f}")
        if symbol_outcome is not None and symbol_outcome.closed_trades > 0:
            details.append(f"learning_symbol_closed_trades={symbol_outcome.closed_trades}")
        if symbol_execution is not None and symbol_execution.attempts > 0:
            details.append(f"learning_execution_attempts={symbol_execution.attempts}")
        return LearningAdjustment(score_delta=score_delta, details=tuple(details))

    def record_order_submission(
        self,
        *,
        market: Market,
        symbol: str,
        strategy_id: str,
        reference_price: Decimal | None,
        fill_price: Decimal | None,
    ) -> None:
        self._update_execution(
            market=market,
            symbol=symbol,
            strategy_id=strategy_id,
            outcome="submitted",
            reference_price=reference_price,
            realized_price=fill_price,
        )

    def record_execution_block(
        self,
        *,
        market: Market,
        symbol: str,
        strategy_id: str,
        reference_price: Decimal | None,
        fresh_price: Decimal | None,
    ) -> None:
        self._update_execution(
            market=market,
            symbol=symbol,
            strategy_id=strategy_id,
            outcome="execution-blocked",
            reference_price=reference_price,
            realized_price=fresh_price,
        )

    def record_order_rejection(
        self,
        *,
        market: Market,
        symbol: str,
        strategy_id: str,
    ) -> None:
        self._update_execution(
            market=market,
            symbol=symbol,
            strategy_id=strategy_id,
            outcome="order-rejected",
            reference_price=None,
            realized_price=None,
        )

    def record_closed_trade(
        self,
        *,
        market: Market,
        trade: NormalizedTrade,
        thesis: dict[str, object],
    ) -> None:
        processed_key = (market, trade.trade_id)
        if processed_key in self._processed_trade_ids:
            return

        strategy_id = str(thesis.get("strategy_id") or "").strip().lower()
        if not strategy_id:
            return
        normalized_symbol = trade.symbol.strip().upper()
        regime = str(thesis.get("regime") or "").strip().lower() or None
        score = _decimal_or_none(thesis.get("score"))
        return_ratio = _trade_return_ratio(trade)

        _update_outcome_stats(self._symbol_outcomes, (market, normalized_symbol, strategy_id), return_ratio, score)
        _update_outcome_stats(self._strategy_outcomes, (market, strategy_id), return_ratio, score)
        if regime is not None:
            _update_outcome_stats(self._regime_outcomes, (market, strategy_id, regime), return_ratio, score)
        self._processed_trade_ids.add(processed_key)

    def analytics_payload(self, market: Market | None = None) -> dict[str, object]:
        strategy_edges: list[_StrategyEdgePayload] = []
        for (stored_market, strategy_id), outcome_stats in self._strategy_outcomes.items():
            if market is not None and stored_market != market:
                continue
            strategy_edges.append(
                {
                    "market": stored_market.value,
                    "strategy_id": strategy_id,
                    "closed_trades": outcome_stats.closed_trades,
                    "win_rate": str(_win_rate(outcome_stats).quantize(Decimal("0.0001"))),
                    "average_return_ratio": str(_average_return_ratio(outcome_stats).quantize(Decimal("0.0001"))),
                    "average_score": (
                        str(_average_score(outcome_stats).quantize(Decimal("0.0001")))
                        if outcome_stats.score_count > 0
                        else None
                    ),
                }
            )

        execution_risks: list[_ExecutionRiskPayload] = []
        for (stored_market, symbol, strategy_id), execution_stats in self._symbol_execution.items():
            if market is not None and stored_market != market:
                continue
            execution_risks.append(
                {
                    "market": stored_market.value,
                    "symbol": symbol,
                    "strategy_id": strategy_id,
                    "attempts": execution_stats.attempts,
                    "orders_submitted": execution_stats.orders_submitted,
                    "execution_blocks": execution_stats.execution_blocks,
                    "order_rejections": execution_stats.order_rejections,
                    "failure_rate": str(_failure_rate(execution_stats).quantize(Decimal("0.0001"))),
                    "average_slippage_ratio": str(_average_slippage_ratio(execution_stats).quantize(Decimal("0.0001"))),
                }
            )

        strategy_edges.sort(
            key=lambda item: (
                item["closed_trades"],
                Decimal(item["average_return_ratio"]),
                item["strategy_id"],
            ),
            reverse=True,
        )
        execution_risks.sort(
            key=lambda item: (
                item["attempts"],
                Decimal(item["failure_rate"]),
                item["symbol"],
            ),
            reverse=True,
        )
        return {
            "closed_trade_observation_count": sum(stats.closed_trades for stats in self._strategy_outcomes.values()),
            "execution_attempt_count": sum(stats.attempts for stats in self._strategy_execution.values()),
            "strategy_edges": strategy_edges[:5],
            "execution_risks": execution_risks[:5],
        }

    def _update_execution(
        self,
        *,
        market: Market,
        symbol: str,
        strategy_id: str,
        outcome: str,
        reference_price: Decimal | None,
        realized_price: Decimal | None,
    ) -> None:
        normalized_symbol = symbol.strip().upper()
        normalized_strategy = strategy_id.strip().lower()
        _update_execution_stats(
            self._symbol_execution,
            (market, normalized_symbol, normalized_strategy),
            outcome,
            reference_price,
            realized_price,
        )
        _update_execution_stats(
            self._strategy_execution,
            (market, normalized_strategy),
            outcome,
            reference_price,
            realized_price,
        )


def _update_outcome_stats(
    store: dict[_KeyT, _OutcomeStats],
    key: _KeyT,
    return_ratio: Decimal,
    score: Decimal | None,
) -> None:
    stats = store.setdefault(key, _OutcomeStats())
    stats.closed_trades += 1
    if return_ratio > Decimal("0"):
        stats.wins += 1
    stats.realized_return_total += return_ratio
    if score is not None:
        stats.score_total += score
        stats.score_count += 1


def _update_execution_stats(
    store: dict[_KeyT, _ExecutionStats],
    key: _KeyT,
    outcome: str,
    reference_price: Decimal | None,
    realized_price: Decimal | None,
) -> None:
    stats = store.setdefault(key, _ExecutionStats())
    stats.attempts += 1
    if outcome == "submitted":
        stats.orders_submitted += 1
    elif outcome == "execution-blocked":
        stats.execution_blocks += 1
    elif outcome == "order-rejected":
        stats.order_rejections += 1

    slippage_ratio = _price_move_ratio(reference_price, realized_price)
    if slippage_ratio is not None:
        stats.slippage_ratio_total += slippage_ratio
        stats.slippage_ratio_count += 1


def _weighted_blend(values: tuple[tuple[Decimal | None, Decimal], ...]) -> Decimal:
    total_weight = Decimal("0")
    total_value = Decimal("0")
    for value, weight in values:
        if value is None:
            continue
        total_value += value * weight
        total_weight += weight
    if total_weight <= Decimal("0"):
        return Decimal("0")
    return total_value / total_weight


def _outcome_score(stats: _OutcomeStats | None) -> Decimal | None:
    if stats is None or stats.closed_trades < 2:
        return None
    average_return = _average_return_ratio(stats)
    win_rate = _win_rate(stats)
    edge_delta = max(Decimal("-0.75"), min(Decimal("0.75"), average_return * Decimal("30")))
    win_delta = max(Decimal("-0.25"), min(Decimal("0.25"), (win_rate - Decimal("0.5")) * Decimal("1.2")))
    return edge_delta + win_delta


def _execution_penalty(stats: _ExecutionStats | None) -> Decimal | None:
    if stats is None or stats.attempts < 2:
        return None
    failure_rate = _failure_rate(stats)
    average_slippage = _average_slippage_ratio(stats)
    return min(Decimal("1.20"), failure_rate * Decimal("1.10") + average_slippage * Decimal("35"))


def _failure_rate(stats: _ExecutionStats) -> Decimal:
    if stats.attempts <= 0:
        return Decimal("0")
    failures = stats.execution_blocks + stats.order_rejections
    return Decimal(failures) / Decimal(stats.attempts)


def _average_slippage_ratio(stats: _ExecutionStats) -> Decimal:
    if stats.slippage_ratio_count <= 0:
        return Decimal("0")
    return stats.slippage_ratio_total / Decimal(stats.slippage_ratio_count)


def _average_return_ratio(stats: _OutcomeStats) -> Decimal:
    if stats.closed_trades <= 0:
        return Decimal("0")
    return stats.realized_return_total / Decimal(stats.closed_trades)


def _win_rate(stats: _OutcomeStats) -> Decimal:
    if stats.closed_trades <= 0:
        return Decimal("0")
    return Decimal(stats.wins) / Decimal(stats.closed_trades)


def _average_score(stats: _OutcomeStats) -> Decimal:
    if stats.score_count <= 0:
        return Decimal("0")
    return stats.score_total / Decimal(stats.score_count)


def _trade_return_ratio(trade: NormalizedTrade) -> Decimal:
    entry_notional = trade.entry_notional
    if entry_notional <= Decimal("0"):
        return Decimal("0")
    return trade.realized_pnl / entry_notional


def _price_move_ratio(reference_price: Decimal | None, realized_price: Decimal | None) -> Decimal | None:
    if reference_price is None or reference_price <= Decimal("0"):
        return None
    if realized_price is None or realized_price <= Decimal("0"):
        return None
    return abs(realized_price - reference_price) / reference_price


def _decimal_or_none(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None