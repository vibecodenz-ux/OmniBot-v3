"""Walk-forward validation helpers built on the live strategy scanner path."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from omnibot_v3.domain import HistoricalBar, Market, PortfolioSnapshot
from omnibot_v3.services.strategy_scanner import StrategyScannerService


@dataclass(frozen=True, slots=True)
class ReplayValidationStep:
    observed_at: datetime
    activity: dict[str, object]
    event_types: tuple[str, ...]
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    portfolio_value: Decimal
    position_count: int
    closed_trade_count: int


@dataclass(frozen=True, slots=True)
class ReplayValidationResult:
    market: Market
    allow_execution: bool
    steps: tuple[ReplayValidationStep, ...]
    signals_seen: int
    orders_submitted: int
    closed_trade_count: int
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    final_portfolio_value: Decimal
    event_counts: dict[str, int]
    analytics: dict[str, object]
    final_activity: dict[str, object]
    final_snapshot: PortfolioSnapshot


@dataclass(slots=True)
class _ReplayRollup:
    steps: int = 0
    signals: int = 0
    orders: int = 0
    brakes: int = 0
    closed_trades: int = 0
    realized_pnl: Decimal = Decimal("0")
    score_total: Decimal = Decimal("0")
    score_count: int = 0


@dataclass(slots=True)
class ScannerReplayValidationService:
    scanner: StrategyScannerService

    def run(
        self,
        market: Market,
        bars_by_symbol: dict[str, tuple[HistoricalBar, ...] | list[HistoricalBar]],
        *,
        allow_execution: bool = True,
    ) -> ReplayValidationResult:
        worker = self.scanner.workers[market]
        store = self.scanner.historical_bar_store
        if store is None:
            raise ValueError("Scanner replay validation requires a historical bar store.")

        grouped: dict[datetime, list[HistoricalBar]] = {}
        for symbol, bars in bars_by_symbol.items():
            normalized_symbol = symbol.strip().upper()
            for bar in sorted(tuple(bars), key=lambda item: item.opened_at):
                grouped.setdefault(bar.closed_at, []).append(
                    HistoricalBar(
                        market=bar.market,
                        symbol=normalized_symbol,
                        timeframe=bar.timeframe,
                        open_price=bar.open_price,
                        high_price=bar.high_price,
                        low_price=bar.low_price,
                        close_price=bar.close_price,
                        volume=bar.volume,
                        opened_at=bar.opened_at,
                        closed_at=bar.closed_at,
                    )
                )

        advance = getattr(worker.adapter, "advance_to", None)
        event_counter: Counter[str] = Counter()
        previous_event_count = len(self.scanner._events[market])
        steps: list[ReplayValidationStep] = []
        regime_stats: dict[str, _ReplayRollup] = {}
        strategy_stats: dict[str, _ReplayRollup] = {}
        replay_regimes: list[str] = []
        replay_strategies: list[str] = []
        previous_closed_trade_count = 0
        previous_realized_pnl = Decimal("0")

        for closed_at in sorted(grouped):
            bars = tuple(sorted(grouped[closed_at], key=lambda item: item.symbol))
            store.save(bars)
            if callable(advance):
                advance(closed_at)
            observed_at = closed_at - timedelta(microseconds=1)
            self.scanner._scan_market(market, allow_execution=allow_execution, observed_at=observed_at)
            snapshot = self.scanner.portfolio_store.load_all().get(market)
            if snapshot is None:
                snapshot = worker.reconcile_portfolio()
            current_events = list(self.scanner._events[market])
            new_event_count = max(len(current_events) - previous_event_count, 0)
            new_event_objects = tuple(current_events[:new_event_count])
            new_events = tuple(event.event_type for event in new_event_objects)
            event_counter.update(new_events)
            previous_event_count = len(current_events)
            activity = self.scanner.activity_payload(market)
            selected_candidate = self._selected_candidate_for_step(new_event_objects, activity)
            self._update_replay_rollups(
                regime_stats=regime_stats,
                strategy_stats=strategy_stats,
                replay_regimes=replay_regimes,
                replay_strategies=replay_strategies,
                selected_candidate=selected_candidate,
                event_types=new_events,
                realized_pnl=snapshot.total_realized_pnl,
                previous_realized_pnl=previous_realized_pnl,
                closed_trade_count=len(snapshot.closed_trades),
                previous_closed_trade_count=previous_closed_trade_count,
            )
            previous_realized_pnl = snapshot.total_realized_pnl
            previous_closed_trade_count = len(snapshot.closed_trades)
            steps.append(
                ReplayValidationStep(
                    observed_at=closed_at,
                    activity=activity,
                    event_types=new_events,
                    realized_pnl=snapshot.total_realized_pnl,
                    unrealized_pnl=snapshot.total_unrealized_pnl,
                    portfolio_value=snapshot.total_portfolio_value,
                    position_count=len(snapshot.positions),
                    closed_trade_count=len(snapshot.closed_trades),
                )
            )

        final_snapshot = self.scanner.portfolio_store.load_all().get(market)
        if final_snapshot is None:
            final_snapshot = worker.reconcile_portfolio()
        final_activity = self.scanner.activity_payload(market)
        return ReplayValidationResult(
            market=market,
            allow_execution=allow_execution,
            steps=tuple(steps),
            signals_seen=_payload_int(final_activity, "signals_seen"),
            orders_submitted=_payload_int(final_activity, "orders_submitted"),
            closed_trade_count=len(final_snapshot.closed_trades),
            realized_pnl=final_snapshot.total_realized_pnl,
            unrealized_pnl=final_snapshot.total_unrealized_pnl,
            final_portfolio_value=final_snapshot.total_portfolio_value,
            event_counts=dict(event_counter),
            analytics={
                **_build_replay_analytics(regime_stats, strategy_stats, replay_regimes, replay_strategies),
                "learning_summary": self.scanner.learning_analytics_payload(market),
            },
            final_activity=final_activity,
            final_snapshot=final_snapshot,
        )

    def _selected_candidate_for_step(
        self,
        new_events: tuple[object, ...],
        activity: dict[str, object],
    ) -> dict[str, object] | None:
        for event in new_events:
            selected_candidate = getattr(event, "selected_candidate", None)
            if isinstance(selected_candidate, dict):
                return selected_candidate
            selected_thesis = getattr(event, "selected_thesis", None)
            if isinstance(selected_thesis, dict):
                return selected_thesis
        selected_candidate = activity.get("last_selected_candidate")
        if isinstance(selected_candidate, dict):
            return selected_candidate
        selected_thesis = activity.get("last_selected_thesis")
        return selected_thesis if isinstance(selected_thesis, dict) else None

    def _update_replay_rollups(
        self,
        *,
        regime_stats: dict[str, _ReplayRollup],
        strategy_stats: dict[str, _ReplayRollup],
        replay_regimes: list[str],
        replay_strategies: list[str],
        selected_candidate: dict[str, object] | None,
        event_types: tuple[str, ...],
        realized_pnl: Decimal,
        previous_realized_pnl: Decimal,
        closed_trade_count: int,
        previous_closed_trade_count: int,
    ) -> None:
        if selected_candidate is None:
            return
        regime = str(selected_candidate.get("regime") or "").strip().lower()
        strategy_id = str(selected_candidate.get("strategy_id") or "").strip().lower()
        score = _decimal_or_none(selected_candidate.get("score"))
        realized_delta = realized_pnl - previous_realized_pnl
        closed_trade_delta = max(closed_trade_count - previous_closed_trade_count, 0)
        if regime:
            replay_regimes.append(regime)
            _update_named_rollup(regime_stats, regime, event_types, realized_delta, closed_trade_delta, score)
        if strategy_id:
            replay_strategies.append(strategy_id)
            _update_named_rollup(strategy_stats, strategy_id, event_types, realized_delta, closed_trade_delta, score)


def _update_named_rollup(
    rollups: dict[str, _ReplayRollup],
    name: str,
    event_types: tuple[str, ...],
    realized_delta: Decimal,
    closed_trade_delta: int,
    score: Decimal | None,
) -> None:
    rollup = rollups.setdefault(name, _ReplayRollup())
    rollup.steps += 1
    rollup.signals += int(
        any(event_type in {"order-submitted", "risk-rejected", "execution-blocked"} for event_type in event_types)
    )
    rollup.orders += event_types.count("order-submitted")
    rollup.brakes += sum(
        1 for event_type in event_types if event_type in {"risk-rejected", "execution-blocked"}
    )
    rollup.closed_trades += closed_trade_delta
    rollup.realized_pnl += realized_delta
    if score is not None:
        rollup.score_total += score
        rollup.score_count += 1


def _build_replay_analytics(
    regime_stats: dict[str, _ReplayRollup],
    strategy_stats: dict[str, _ReplayRollup],
    replay_regimes: list[str],
    replay_strategies: list[str],
) -> dict[str, object]:
    return {
        "regime_rollups": _serialize_replay_rollups(regime_stats),
        "strategy_rollups": _serialize_replay_rollups(strategy_stats),
        "drift_summary": {
            "dominant_regime": Counter(replay_regimes).most_common(1)[0][0] if replay_regimes else None,
            "dominant_strategy": Counter(replay_strategies).most_common(1)[0][0] if replay_strategies else None,
            "opening_regime": replay_regimes[0] if replay_regimes else None,
            "closing_regime": replay_regimes[-1] if replay_regimes else None,
            "opening_strategy": replay_strategies[0] if replay_strategies else None,
            "closing_strategy": replay_strategies[-1] if replay_strategies else None,
            "regime_shift_count": _rotation_count(replay_regimes),
            "strategy_rotation_count": _rotation_count(replay_strategies),
        },
    }


def _serialize_replay_rollups(rollups: dict[str, _ReplayRollup]) -> list[dict[str, object]]:
    serialized: list[dict[str, object]] = []
    for name, values in sorted(rollups.items(), key=lambda item: (item[1].steps, item[0]), reverse=True):
        score_count = values.score_count
        average_score = (
            (values.score_total / Decimal(score_count)).quantize(Decimal("0.0001"))
            if score_count > 0
            else None
        )
        serialized.append(
            {
                "name": name,
                "steps": values.steps,
                "signals": values.signals,
                "orders": values.orders,
                "brakes": values.brakes,
                "closed_trades": values.closed_trades,
                "realized_pnl": str(values.realized_pnl.quantize(Decimal("0.0001"))),
                "average_score": str(average_score) if average_score is not None else None,
            }
        )
    return serialized


def _payload_int(payload: dict[str, object], key: str) -> int:
    value = payload.get(key)
    if isinstance(value, int):
        return value
    return int(str(value or 0))


def _rotation_count(values: list[str]) -> int:
    if len(values) < 2:
        return 0
    return sum(1 for previous, current in zip(values, values[1:], strict=False) if previous != current)


def _decimal_or_none(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None