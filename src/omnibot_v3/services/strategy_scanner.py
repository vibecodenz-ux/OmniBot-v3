"""Background strategy scanning and optional order execution for runtime markets."""

from __future__ import annotations

import threading
from collections import Counter, deque
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Protocol, cast

from omnibot_v3.domain import (
    BarTimeframe,
    HistoricalBar,
    Market,
    NormalizedAccount,
    NormalizedPosition,
    OrderRequest,
    StrategyContext,
    StrategyExecutionResult,
)
from omnibot_v3.services.market_catalog import normalize_profile_id, normalize_strategy_id
from omnibot_v3.services.market_data_store import HistoricalBarStore
from omnibot_v3.services.market_hours import MarketHoursService
from omnibot_v3.services.market_worker import MarketWorker
from omnibot_v3.services.operator_state import OperatorStateService
from omnibot_v3.services.orchestrator import TradingOrchestrator
from omnibot_v3.services.runtime_store import PortfolioSnapshotStore
from omnibot_v3.services.scanner_feedback import (
    ScannerFeedback,
)
from omnibot_v3.services.scanner_feedback import (
    execution_summary as _execution_summary,
)
from omnibot_v3.services.scanner_learning import ScannerLearningState
from omnibot_v3.services.scanner_market_policy import policy_for_market
from omnibot_v3.services.scanner_runtime_support import (
    build_strategy_profile as _strategy_profile,
)
from omnibot_v3.services.scanner_runtime_support import (
    evaluate_execution_quality as _evaluate_execution_quality,
)
from omnibot_v3.services.scanner_runtime_support import (
    evaluate_portfolio_controls as _evaluate_portfolio_controls,
)
from omnibot_v3.services.scanner_runtime_support import (
    execution_mode_for_profile as _execution_mode_for_profile,
)
from omnibot_v3.services.scanner_runtime_support import (
    in_symbol_cooldown as _in_symbol_cooldown,
)
from omnibot_v3.services.scanner_symbol_evaluator import RollingSignalPlugin, ScannerSymbolEvaluator


class SelectionProvider(Protocol):
    def __call__(self, market: Market) -> tuple[str, str]:
        """Return the selected strategy_id and profile_id for a market."""


@dataclass(frozen=True, slots=True)
class ScannerActivity:
    market: Market
    automation_state: str = "connected-only"
    execution_mode: str = "scan-and-trade"
    scanner_running: bool = False
    warmup_status: str = "pending"
    signals_seen: int = 0
    orders_submitted: int = 0
    last_scan_at: datetime | None = None
    last_signal_at: datetime | None = None
    last_order_at: datetime | None = None
    warmup_completed_at: datetime | None = None
    last_decision: str = "Scanner idle."
    last_signal_symbol: str | None = None
    last_order_id: str | None = None
    last_error: str | None = None
    last_price: str | None = None
    candidate_count: int = 0
    candidate_score: str | None = None
    last_selected_candidate: dict[str, object] | None = None
    last_selected_thesis: dict[str, object] | None = None
    considered_candidates: tuple[dict[str, object], ...] = ()


@dataclass(frozen=True, slots=True)
class ScannerEvent:
    market: Market
    message: str
    occurred_at: datetime
    event_type: str
    level: str = "info"
    symbol: str | None = None
    strategy_id: str | None = None
    profile_id: str | None = None
    price: str | None = None
    details: tuple[str, ...] = ()
    candidate_count: int = 0
    candidate_score: str | None = None
    selected_candidate: dict[str, object] | None = None
    selected_thesis: dict[str, object] | None = None
    considered_candidates: tuple[dict[str, object], ...] = ()


@dataclass(frozen=True, slots=True)
class _RankedSymbol:
    symbol: str
    score: Decimal
    latest_price: Decimal
    momentum_ratio: Decimal
    volatility_ratio: Decimal
    volume_ratio: Decimal
    bar_count: int


@dataclass(frozen=True, slots=True)
class _PositionOverlay:
    symbol: str
    side: str
    entry_price: Decimal
    close_target_price: Decimal | None
    thesis_id: str | None = None
    strategy_id: str | None = None


@dataclass(slots=True)
class StrategyScannerService:
    orchestrator: TradingOrchestrator
    workers: dict[Market, MarketWorker]
    portfolio_store: PortfolioSnapshotStore
    selection_provider: SelectionProvider
    operator_state_service: OperatorStateService | None = None
    quote_provider: Callable[[Market, str], Decimal | None] | None = None
    market_hours: MarketHoursService = field(default_factory=MarketHoursService)
    historical_bar_store: HistoricalBarStore | None = None
    bar_timeframe: BarTimeframe = BarTimeframe.FIVE_MINUTES
    snapshot_ttl_seconds: int = 20
    quote_ttl_seconds: int = 8
    warmup_bars: int = 24
    ranking_top_n: int = 5
    history_refresh_interval_seconds: int = 900
    _activities: dict[Market, ScannerActivity] = field(default_factory=dict, init=False)
    _threads: dict[Market, threading.Thread] = field(default_factory=dict, init=False)
    _stop_events: dict[Market, threading.Event] = field(default_factory=dict, init=False)
    _price_history: dict[tuple[Market, str], deque[Decimal]] = field(default_factory=dict, init=False)
    _last_trade_at: dict[tuple[Market, str], datetime] = field(default_factory=dict, init=False)
    _last_history_refresh_at: dict[tuple[Market, str], datetime] = field(default_factory=dict, init=False)
    _quote_cache: dict[tuple[Market, str], tuple[Decimal, datetime]] = field(default_factory=dict, init=False)
    _events: dict[Market, deque[ScannerEvent]] = field(default_factory=dict, init=False)
    _rankings: dict[Market, tuple[_RankedSymbol, ...]] = field(default_factory=dict, init=False)
    _position_opened_at: dict[tuple[Market, str], datetime] = field(default_factory=dict, init=False)
    _selected_theses: dict[tuple[Market, str], dict[str, object]] = field(default_factory=dict, init=False)
    _learning_state: ScannerLearningState = field(default_factory=ScannerLearningState, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)

    def __post_init__(self) -> None:
        if self.historical_bar_store is None:
            from omnibot_v3.infra.market_data_store import InMemoryHistoricalBarStore

            self.historical_bar_store = InMemoryHistoricalBarStore()
        for market in (Market.STOCKS, Market.CRYPTO, Market.FOREX):
            self._activities[market] = ScannerActivity(
                market=market,
                execution_mode="scan-and-trade",
            )
            self._events[market] = deque(maxlen=40)
            self._rankings[market] = ()

    def start_market(self, market: Market) -> None:
        with self._lock:
            existing = self._threads.get(market)
            if existing is not None and existing.is_alive():
                self._set_activity_locked(market, scanner_running=True)
                return

            stop_event = threading.Event()
            thread = threading.Thread(
                target=self._run_market_loop,
                args=(market, stop_event),
                daemon=True,
                name=f"omnibot-scanner-{market.value}",
            )
            self._stop_events[market] = stop_event
            self._threads[market] = thread
            self._set_activity_locked(
                market,
                scanner_running=True,
                automation_state="warming-up",
                warmup_status="pending",
                last_decision="Scanner started and market data warmup queued.",
                last_error=None,
            )
            self._record_event_locked(
                market,
                message="Scanner started for live market data and strategy evaluation.",
                event_type="scanner-started",
                occurred_at=datetime.now(UTC),
            )
            thread.start()

    def stop_market(self, market: Market) -> None:
        thread: threading.Thread | None = None
        with self._lock:
            stop_event = self._stop_events.get(market)
            if stop_event is not None:
                stop_event.set()
            thread = self._threads.get(market)
            self._set_activity_locked(
                market,
                scanner_running=False,
                automation_state="connected-only",
                last_decision="Scanner stopped.",
            )
            self._record_event_locked(
                market,
                message="Scanner stopped.",
                event_type="scanner-stopped",
                occurred_at=datetime.now(UTC),
            )
        if thread is not None:
            thread.join(timeout=1.5)

    def stop_all(self) -> None:
        for market in tuple(self.workers.keys()):
            self.stop_market(market)

    def activity_payload(self, market: Market) -> dict[str, object]:
        with self._lock:
            activity = self._activities[market]
        return {
            "automation_state": activity.automation_state,
            "execution_mode": activity.execution_mode,
            "scanner_running": activity.scanner_running,
            "warmup_status": activity.warmup_status,
            "signals_seen": activity.signals_seen,
            "orders_submitted": activity.orders_submitted,
            "last_scan_at": activity.last_scan_at.isoformat() if activity.last_scan_at else None,
            "last_signal_at": activity.last_signal_at.isoformat() if activity.last_signal_at else None,
            "last_order_at": activity.last_order_at.isoformat() if activity.last_order_at else None,
            "warmup_completed_at": activity.warmup_completed_at.isoformat() if activity.warmup_completed_at else None,
            "last_decision": activity.last_decision,
            "last_signal_symbol": activity.last_signal_symbol,
            "last_order_id": activity.last_order_id,
            "last_error": activity.last_error,
            "last_price": activity.last_price,
            "candidate_count": activity.candidate_count,
            "candidate_score": activity.candidate_score,
            "last_selected_candidate": activity.last_selected_candidate,
            "last_selected_thesis": activity.last_selected_thesis,
            "considered_candidates": list(activity.considered_candidates),
            "top_ranked_symbol": self._rankings[market][0].symbol if self._rankings[market] else None,
            "ranked_symbols": [item.symbol for item in self._rankings[market][: self.ranking_top_n]],
        }

    def decision_log_payload(self) -> dict[str, object]:
        with self._lock:
            events = [event for market in (Market.STOCKS, Market.CRYPTO, Market.FOREX) for event in self._events[market]]
            activities = dict(self._activities)
            rankings = dict(self._rankings)
        events.sort(key=lambda item: item.occurred_at, reverse=True)
        recent_events = events[:40]
        market_summaries = [
            self._market_summary_payload(market, activities[market], rankings.get(market, ()))
            for market in (Market.STOCKS, Market.CRYPTO, Market.FOREX)
        ]
        return {
            "generated_at": datetime.now(UTC).isoformat(),
            "events": [
                {
                    "market": event.market.value,
                    "message": event.message,
                    "occurred_at": event.occurred_at.isoformat(),
                    "event_type": event.event_type,
                    "level": event.level,
                    "symbol": event.symbol,
                    "strategy_id": event.strategy_id,
                    "profile_id": event.profile_id,
                    "price": event.price,
                    "details": list(event.details),
                    "candidate_count": event.candidate_count,
                    "candidate_score": event.candidate_score,
                    "selected_candidate": event.selected_candidate,
                    "selected_thesis": event.selected_thesis,
                    "considered_candidates": list(event.considered_candidates),
                }
                for event in recent_events
            ],
            "market_summaries": market_summaries,
            "analytics": self._analytics_payload(recent_events, market_summaries, activities),
        }

    def learning_analytics_payload(self, market: Market | None = None) -> dict[str, object]:
        return self._learning_state.analytics_payload(market)

    def selected_thesis_for(self, market: Market, symbol: str) -> dict[str, object] | None:
        normalized_symbol = symbol.strip().upper()
        with self._lock:
            thesis = self._selected_theses.get((market, normalized_symbol))
        if thesis is not None:
            return dict(thesis)
        if self.operator_state_service is None:
            return None
        return self.operator_state_service.get_active_trade_thesis(market, normalized_symbol)

    def _run_market_loop(self, market: Market, stop_event: threading.Event) -> None:
        worker = self.workers[market]
        while not stop_event.is_set():
            try:
                snapshot = self.orchestrator.snapshot(market)
                self._scan_market(market, allow_execution=snapshot.state.value == "RUNNING")
            except Exception as exc:
                with self._lock:
                    self._set_activity_locked(
                        market,
                        automation_state="scan-error",
                        scanner_running=True,
                        last_error=str(exc),
                        last_decision=f"Scanner error: {exc}",
                        last_scan_at=datetime.now(UTC),
                    )
            stop_event.wait(max(1, worker.settings.poll_interval_seconds))

        with self._lock:
            self._threads.pop(market, None)
            self._stop_events.pop(market, None)

    def _scan_market(
        self,
        market: Market,
        *,
        allow_execution: bool,
        observed_at: datetime | None = None,
    ) -> None:
        worker = self.workers[market]
        validation = worker.validate_configuration()
        now = observed_at or datetime.now(UTC)
        execution_mode = "scan-and-trade" if allow_execution else "scan-only"
        if not validation.valid:
            self.orchestrator.heartbeat(market)
            with self._lock:
                self._set_activity_locked(
                    market,
                    automation_state="awaiting-credentials",
                    execution_mode=execution_mode,
                    scanner_running=True,
                    last_decision="; ".join(validation.errors),
                    last_scan_at=now,
                )
                if allow_execution:
                    self._record_event_locked(
                        market,
                        message=f"Configuration blocked trading: {'; '.join(validation.errors)}",
                        event_type="validation-error",
                        occurred_at=now,
                        level="warning",
                    )
            return

        market_status = self.market_hours.status_for(market, now)
        warmup_status = self._warmup_market_history(market, worker, now)
        if not market_status.is_open:
            self.orchestrator.heartbeat(market)
            detail = market_status.detail
            resolved_warmup_status = self._resolved_warmup_status(market, worker, fallback=warmup_status)
            with self._lock:
                self._set_activity_locked(
                    market,
                    automation_state="actively-scanning" if allow_execution else "passive-scanning",
                    execution_mode=execution_mode,
                    warmup_status=resolved_warmup_status,
                    scanner_running=True,
                    last_decision=detail,
                    last_scan_at=now,
                )
            return

        if not allow_execution:
            latest_prices = self._prices_for_symbols(worker, market, tuple(worker.settings.symbols), now)
            for symbol, price in latest_prices.items():
                if price <= Decimal("0"):
                    continue
                self._record_live_bar(market, symbol, price, now)
                self._history_for(market, symbol).append(price)
            ranked_symbols = self._rank_symbols(market, tuple(worker.settings.symbols), latest_prices)
            resolved_warmup_status = self._resolved_warmup_status(market, worker, fallback=warmup_status)
            with self._lock:
                self._rankings[market] = tuple(ranked_symbols[: self.ranking_top_n])
                self._set_activity_locked(
                    market,
                    automation_state="passive-scanning",
                    execution_mode=execution_mode,
                    scanner_running=True,
                    warmup_status=resolved_warmup_status,
                    warmup_completed_at=now if resolved_warmup_status == "ready" else None,
                    last_scan_at=now,
                    last_decision=(
                        f"Passive market data refresh completed for {len(latest_prices)} symbols."
                        if latest_prices
                        else "Passive market data refresh is waiting for broker quotes."
                    ),
                    last_error=None,
                    last_signal_symbol=ranked_symbols[0].symbol if ranked_symbols else None,
                    last_price=str(ranked_symbols[0].latest_price) if ranked_symbols else None,
                )
            return

        snapshot = self._portfolio_snapshot_for_scan(market, worker, now)
        self.portfolio_store.save(snapshot)
        self._sync_position_open_times(market, snapshot.positions, now)
        self._sync_trade_thesis_state(market, snapshot.positions, snapshot.closed_trades)
        worker_status = worker.status()
        self.orchestrator.heartbeat(market, last_reconciled_at=worker_status.last_reconciled_at)

        _, profile_id = self.selection_provider(market)
        profile_id = normalize_profile_id(market, profile_id)
        execution_mode = _execution_mode_for_profile(profile_id)
        latest_prices = self._prices_for_symbols(worker, market, tuple(worker.settings.symbols), now)
        ranked_symbols = self._rank_symbols(market, tuple(worker.settings.symbols), latest_prices)
        evaluation = self._evaluate_symbols(
            market,
            worker,
            snapshot.account,
            snapshot.positions,
            profile_id,
            latest_prices,
            ranked_symbols,
            now,
        )
        resolved_warmup_status = self._resolved_warmup_status(market, worker, fallback=warmup_status)
        with self._lock:
            self._rankings[market] = tuple(ranked_symbols[: self.ranking_top_n])
        activity_updates: dict[str, object] = {
            "automation_state": "actively-scanning",
            "execution_mode": execution_mode,
            "scanner_running": True,
            "warmup_status": resolved_warmup_status,
            "warmup_completed_at": now if resolved_warmup_status == "ready" else None,
            "last_scan_at": now,
            "last_decision": str(evaluation.get("decision") or "Scan completed."),
            "last_error": evaluation.get("error"),
            "last_signal_symbol": evaluation.get("signal_symbol"),
            "last_price": evaluation.get("price"),
            "candidate_count": evaluation.get("candidate_count", 0),
            "candidate_score": evaluation.get("candidate_score"),
            "last_selected_candidate": evaluation.get("selected_candidate"),
            "last_selected_thesis": evaluation.get("selected_thesis"),
            "considered_candidates": evaluation.get("considered_candidates", ()),
        }
        if evaluation.get("signal_detected"):
            with self._lock:
                current = self._activities[market]
                activity_updates["signals_seen"] = current.signals_seen + 1
                activity_updates["last_signal_at"] = now

        order_request = evaluation.get("order_request")
        event_details = cast(tuple[str, ...], evaluation.get("details") or ())
        evaluation_strategy_id = str(evaluation.get("strategy_id") or "auto")
        candidate_count = _coerce_int(evaluation.get("candidate_count", 0))
        candidate_score = _coerce_optional_str(evaluation.get("candidate_score"))
        selected_candidate = _coerce_optional_dict(evaluation.get("selected_candidate"))
        selected_thesis = _coerce_optional_dict(evaluation.get("selected_thesis"))
        considered_candidates = _coerce_dict_sequence(evaluation.get("considered_candidates"))
        thesis_update = _coerce_optional_dict(evaluation.get("thesis_update"))
        if thesis_update is not None:
            thesis_symbol = str(evaluation.get("signal_symbol") or "").strip().upper()
            if thesis_symbol:
                with self._lock:
                    self._selected_theses[(market, thesis_symbol)] = thesis_update
                if self.operator_state_service is not None:
                    self.operator_state_service.upsert_active_trade_thesis(
                        market,
                        thesis_symbol,
                        thesis_update,
                        now=now,
                    )
                activity_updates["last_selected_thesis"] = thesis_update
                with self._lock:
                    self._record_event_locked(
                        market,
                        message=str(evaluation.get("decision") or "Thesis updated."),
                        event_type="thesis-updated",
                        occurred_at=now,
                        symbol=thesis_symbol,
                        strategy_id=evaluation_strategy_id,
                        profile_id=profile_id,
                        price=str(evaluation.get("price") or ""),
                        details=event_details,
                        candidate_count=candidate_count,
                        candidate_score=candidate_score,
                        selected_candidate=selected_candidate,
                        selected_thesis=thesis_update,
                        considered_candidates=considered_candidates,
                    )
        if isinstance(order_request, OrderRequest) and execution_mode == "scan-and-trade":
            fresh_price = self._price_for_symbol(
                worker,
                market,
                str(evaluation.get("signal_symbol") or order_request.symbol),
                now,
                force_refresh=True,
            )
            execution_quality = _evaluate_execution_quality(
                market=market,
                order_request=order_request,
                fresh_price=fresh_price,
                profile_id=profile_id,
            )
            if not execution_quality.accepted:
                rejected_symbol = str(evaluation.get("signal_symbol") or order_request.symbol)
                activity_updates["last_decision"] = _execution_summary(rejected_symbol, execution_quality.reason)
                self._learning_state.record_execution_block(
                    market=market,
                    symbol=rejected_symbol,
                    strategy_id=evaluation_strategy_id,
                    reference_price=order_request.limit_price,
                    fresh_price=fresh_price,
                )
                with self._lock:
                    self._record_event_locked(
                        market,
                        message=str(activity_updates["last_decision"]),
                        event_type="execution-blocked",
                        occurred_at=now,
                        level="warning",
                        symbol=rejected_symbol,
                        strategy_id=evaluation_strategy_id,
                        profile_id=profile_id,
                        price=str(fresh_price or evaluation.get("price") or order_request.limit_price or ""),
                        details=(
                            *event_details,
                            f"execution_quality={execution_quality.reason}",
                            f"evaluated_price={order_request.limit_price}",
                            f"fresh_price={fresh_price}",
                        ),
                        candidate_count=candidate_count,
                        candidate_score=candidate_score,
                        selected_candidate=selected_candidate,
                        selected_thesis=selected_thesis,
                        considered_candidates=considered_candidates,
                    )
            else:
                if fresh_price is not None and fresh_price > Decimal("0"):
                    order_request = replace(order_request, limit_price=fresh_price)
                portfolio_control = _evaluate_portfolio_controls(snapshot, order_request, profile_id, now)
                if not portfolio_control.accepted:
                    activity_updates["last_decision"] = _execution_summary(
                        str(evaluation.get("signal_symbol") or order_request.symbol),
                        portfolio_control.reason,
                    )
                    with self._lock:
                        self._record_event_locked(
                            market,
                            message=str(activity_updates["last_decision"]),
                            event_type="risk-rejected",
                            occurred_at=now,
                            level="warning",
                            symbol=str(evaluation.get("signal_symbol") or order_request.symbol),
                            strategy_id=evaluation_strategy_id,
                            profile_id=profile_id,
                            price=str(evaluation.get("price") or order_request.limit_price or ""),
                            details=(*event_details, f"portfolio_control={portfolio_control.reason}"),
                            candidate_count=candidate_count,
                            candidate_score=candidate_score,
                            selected_candidate=selected_candidate,
                            selected_thesis=selected_thesis,
                            considered_candidates=considered_candidates,
                        )
                else:
                    try:
                        order = worker.submit_order(order_request)
                        refreshed_snapshot = worker.reconcile_portfolio()
                    except Exception as exc:
                        rejected_symbol = str(evaluation.get("signal_symbol") or order_request.symbol)
                        activity_updates["last_error"] = str(exc)
                        activity_updates["last_decision"] = _execution_summary(rejected_symbol, f"order rejected: {exc}")
                        self._learning_state.record_order_rejection(
                            market=market,
                            symbol=rejected_symbol,
                            strategy_id=evaluation_strategy_id,
                        )
                        with self._lock:
                            self._record_event_locked(
                                market,
                                message=str(activity_updates["last_decision"]),
                                event_type="order-rejected",
                                occurred_at=now,
                                level="warning",
                                symbol=rejected_symbol,
                                strategy_id=evaluation_strategy_id,
                                profile_id=profile_id,
                                price=str(evaluation.get("price") or order_request.limit_price or ""),
                                details=event_details,
                                candidate_count=candidate_count,
                                candidate_score=candidate_score,
                                selected_candidate=selected_candidate,
                                selected_thesis=selected_thesis,
                                considered_candidates=considered_candidates,
                            )
                    else:
                        self.portfolio_store.save(refreshed_snapshot)
                        self._learning_state.record_order_submission(
                            market=market,
                            symbol=order.symbol,
                            strategy_id=evaluation_strategy_id,
                            reference_price=order_request.limit_price,
                            fill_price=order.average_fill_price,
                        )
                        thesis_transition_state = _coerce_optional_str(evaluation.get("thesis_transition_state"))
                        thesis_transition_reason = _coerce_optional_str(evaluation.get("thesis_transition_reason"))
                        if (
                            self.operator_state_service is not None
                            and thesis_transition_state is not None
                            and thesis_transition_reason is not None
                        ):
                            self.operator_state_service.transition_active_trade_thesis(
                                market,
                                order.symbol,
                                state=thesis_transition_state,
                                reason=thesis_transition_reason,
                                transitioned_at=now,
                            )
                        if self.operator_state_service is not None and selected_thesis is not None:
                            persisted_active_thesis = self.operator_state_service.get_active_trade_thesis(market, order.symbol)
                            merged_selected_thesis = dict(selected_thesis)
                            if isinstance(persisted_active_thesis, dict):
                                persisted_archived_scale_out_count = _coerce_int(
                                    persisted_active_thesis.get("archived_scale_out_count", 0)
                                )
                                merged_archived_scale_out_count = _coerce_int(
                                    merged_selected_thesis.get("archived_scale_out_count", 0)
                                )
                                if persisted_archived_scale_out_count > merged_archived_scale_out_count:
                                    merged_selected_thesis["archived_scale_out_count"] = persisted_archived_scale_out_count
                            self.operator_state_service.upsert_active_trade_thesis(
                                market,
                                order.symbol,
                                merged_selected_thesis,
                                now=now,
                            )
                            selected_thesis = merged_selected_thesis
                        self._sync_trade_thesis_state(market, refreshed_snapshot.positions, refreshed_snapshot.closed_trades)
                        worker_status = worker.status()
                        self.orchestrator.heartbeat(market, last_reconciled_at=worker_status.last_reconciled_at)
                        position_still_active = any(
                            position.symbol.upper() == order.symbol.upper() for position in refreshed_snapshot.positions
                        )
                        with self._lock:
                            if selected_thesis is not None and position_still_active:
                                persisted_active_thesis = (
                                    self.operator_state_service.get_active_trade_thesis(market, order.symbol)
                                    if self.operator_state_service is not None
                                    else None
                                )
                                merged_selected_thesis = dict(selected_thesis)
                                if isinstance(persisted_active_thesis, dict):
                                    persisted_archived_scale_out_count = _coerce_int(
                                        persisted_active_thesis.get("archived_scale_out_count", 0)
                                    )
                                    merged_archived_scale_out_count = _coerce_int(
                                        merged_selected_thesis.get("archived_scale_out_count", 0)
                                    )
                                    if persisted_archived_scale_out_count > merged_archived_scale_out_count:
                                        merged_selected_thesis["archived_scale_out_count"] = persisted_archived_scale_out_count
                                self._selected_theses[(market, order.symbol.upper())] = merged_selected_thesis
                                if self.operator_state_service is not None:
                                    self.operator_state_service.upsert_active_trade_thesis(
                                        market,
                                        order.symbol,
                                        merged_selected_thesis,
                                    )
                            current = self._activities[market]
                            activity_updates["orders_submitted"] = current.orders_submitted + 1
                        activity_updates["last_order_at"] = now
                        activity_updates["last_order_id"] = order.order_id
                        activity_updates["last_decision"] = f"Submitted {order.side.value.upper()} {order.symbol} from {evaluation_strategy_id}."
                        with self._lock:
                            self._record_event_locked(
                                market,
                                message=str(activity_updates["last_decision"]),
                                event_type="order-submitted",
                                occurred_at=now,
                                symbol=order.symbol,
                                strategy_id=evaluation_strategy_id,
                                profile_id=profile_id,
                                price=str(order.average_fill_price or order_request.limit_price or ""),
                                details=event_details,
                                candidate_count=candidate_count,
                                candidate_score=candidate_score,
                                selected_candidate=selected_candidate,
                                selected_thesis=selected_thesis,
                                considered_candidates=considered_candidates,
                            )

        with self._lock:
            self._set_activity_locked(market, **activity_updates)

    def _evaluate_symbols(
        self,
        market: Market,
        worker: MarketWorker,
        account: NormalizedAccount,
        positions: tuple[NormalizedPosition, ...],
        profile_id: str,
        latest_prices: dict[str, Decimal],
        ranked_symbols: list[_RankedSymbol],
        observed_at: datetime,
    ) -> dict[str, object]:
        ordered_symbols = tuple(item.symbol for item in ranked_symbols) or tuple(worker.settings.symbols)

        def record_feedback(feedback: ScannerFeedback, symbol: str, price: Decimal | None, strategy_for_event: str) -> None:
            with self._lock:
                self._record_event_locked(
                    market,
                    message=feedback.message,
                    event_type=feedback.event_type,
                    occurred_at=observed_at,
                    level=feedback.level,
                    symbol=symbol,
                    strategy_id=strategy_for_event,
                    profile_id=profile_id,
                    price=str(price) if price is not None else None,
                    details=feedback.details,
                )

        return ScannerSymbolEvaluator(
            market=market,
            account=account,
            positions=positions,
            profile_id=profile_id,
            latest_prices=latest_prices,
            ordered_symbols=ordered_symbols,
            observed_at=observed_at,
            record_live_bar=self._record_live_bar,
            history_for=self._history_for,
            recent_bars_for=lambda requested_market, symbol, limit: self._recent_bars_for(
                requested_market,
                symbol,
                limit=limit,
            ),
            position_opened_at_for=lambda symbol: self._position_opened_at.get((market, symbol.upper())),
            selected_thesis_for=lambda symbol: self.selected_thesis_for(market, symbol),
            record_feedback=record_feedback,
            cooldown_active=lambda symbol: _in_symbol_cooldown(self._last_trade_at.get((market, symbol)), profile_id),
            mark_trade_at=lambda symbol, occurred_at: self._last_trade_at.__setitem__((market, symbol), occurred_at),
            learning_adjustment_for=lambda symbol, strategy_id, result: self._learning_adjustment_for(
                market,
                symbol,
                strategy_id,
                result,
            ),
        ).evaluate()

    def _learning_adjustment_for(
        self,
        market: Market,
        symbol: str,
        strategy_id: str,
        result: StrategyExecutionResult,
    ) -> tuple[Decimal, tuple[str, ...]]:
        regime = result.regime.regime.value if result.regime is not None else None
        adjustment = self._learning_state.score_adjustment(
            market=market,
            symbol=symbol,
            strategy_id=strategy_id,
            regime=regime,
        )
        return adjustment.score_delta, adjustment.details

    def _portfolio_snapshot_for_scan(
        self,
        market: Market,
        worker: MarketWorker,
        observed_at: datetime,
    ):
        current = self.portfolio_store.load_all().get(market)
        if current is not None and observed_at - current.as_of <= timedelta(seconds=self.snapshot_ttl_seconds):
            return current
        return worker.reconcile_portfolio()

    def _price_for_symbol(
        self,
        worker: MarketWorker,
        market: Market,
        symbol: str,
        observed_at: datetime,
        *,
        force_refresh: bool = False,
    ) -> Decimal | None:
        return self._prices_for_symbols(worker, market, (symbol,), observed_at, force_refresh=force_refresh).get(symbol)

    def _prices_for_symbols(
        self,
        worker: MarketWorker,
        market: Market,
        symbols: tuple[str, ...],
        observed_at: datetime,
        *,
        force_refresh: bool = False,
    ) -> dict[str, Decimal]:
        resolved: dict[str, Decimal] = {}
        missing_symbols: list[str] = []
        cache_ttl_seconds = self.quote_ttl_seconds
        if cache_ttl_seconds > 0:
            cache_ttl_seconds = max(cache_ttl_seconds, _recommended_quote_ttl_seconds(worker))

        for symbol in symbols:
            cache_key = (market, symbol)
            cached = self._quote_cache.get(cache_key)
            if (
                not force_refresh
                and cached is not None
                and observed_at - cached[1] <= timedelta(seconds=cache_ttl_seconds)
            ):
                resolved[symbol] = cached[0]
            else:
                missing_symbols.append(symbol)

        fetched: dict[str, Decimal | None] = {}
        if missing_symbols:
            if self.quote_provider is not None:
                fetched = {symbol: self.quote_provider(market, symbol) for symbol in missing_symbols}
            else:
                batch_getter = getattr(worker.adapter, "get_latest_prices", None)
                if callable(batch_getter):
                    batch_prices = batch_getter(tuple(missing_symbols))
                    if isinstance(batch_prices, dict):
                        for symbol, price in batch_prices.items():
                            if isinstance(price, Decimal) and price > Decimal("0"):
                                fetched[symbol] = price
                price_getter = getattr(worker.adapter, "get_latest_price", None)
                for symbol in missing_symbols:
                    if symbol in fetched:
                        continue
                    fetched[symbol] = price_getter(symbol) if callable(price_getter) else None

        for symbol, price in fetched.items():
            if price is not None and price > Decimal("0"):
                resolved[symbol] = price
                self._quote_cache[(market, symbol)] = (price, observed_at)

        return resolved

    def _history_for(self, market: Market, symbol: str) -> deque[Decimal]:
        key = (market, symbol)
        history = self._price_history.get(key)
        if history is None:
            history = deque(maxlen=12)
            self._price_history[key] = history
        return history

    def _warmup_market_history(
        self,
        market: Market,
        worker: MarketWorker,
        observed_at: datetime,
    ) -> str:
        adapter_fetch = getattr(worker.adapter, "get_historical_bars", None)
        historical_bar_store = self.historical_bar_store
        minimum_bars = 3
        ready_symbols = 0
        populated_symbols = 0
        stale_threshold = _timeframe_delta(self.bar_timeframe) * 2
        for symbol in worker.settings.symbols:
            bars = list(self._bars_for(market, symbol, limit=self.warmup_bars))
            refresh_key = (market, symbol)
            last_refresh = self._last_history_refresh_at.get(refresh_key)
            latest_bar = bars[-1] if bars else None
            history_is_stale = latest_bar is None or latest_bar.closed_at < observed_at - stale_threshold
            should_refresh = (
                callable(adapter_fetch)
                and (last_refresh is None or observed_at - last_refresh >= timedelta(seconds=self.history_refresh_interval_seconds))
                and (len(bars) < self.warmup_bars or history_is_stale)
            )
            if should_refresh:
                fetch_historical_bars = cast(Callable[..., object], adapter_fetch)
                fetched = fetch_historical_bars(symbol, self.bar_timeframe, limit=self.warmup_bars)
                fetched_bars = tuple(fetched) if isinstance(fetched, (list, tuple)) else ()
                if fetched_bars and historical_bar_store is not None:
                    historical_bar_store.save(fetched_bars)
                    bars = list(self._bars_for(market, symbol, limit=self.warmup_bars))
                self._last_history_refresh_at[refresh_key] = observed_at
            if bars:
                populated_symbols += 1
            if len(bars) >= minimum_bars:
                self._hydrate_price_history_from_bars(market, symbol, tuple(bars))
            if len(bars) >= minimum_bars or len(self._history_for(market, symbol)) >= minimum_bars:
                ready_symbols += 1
        if ready_symbols == len(worker.settings.symbols) and ready_symbols > 0:
            return "ready"
        if populated_symbols > 0:
            return "partial"
        return "collecting-live-data"

    def _resolved_warmup_status(
        self,
        market: Market,
        worker: MarketWorker,
        *,
        fallback: str,
    ) -> str:
        ready_symbols = 0
        partial_symbols = 0
        for symbol in worker.settings.symbols:
            if len(self._bars_for(market, symbol, limit=3)) >= 3 or len(self._history_for(market, symbol)) >= 3:
                ready_symbols += 1
            elif self._bars_for(market, symbol, limit=1) or self._history_for(market, symbol):
                partial_symbols += 1
        if ready_symbols == len(worker.settings.symbols) and ready_symbols > 0:
            return "ready"
        if ready_symbols > 0 or partial_symbols > 0:
            return "partial"
        return fallback

    def _record_live_bar(
        self,
        market: Market,
        symbol: str,
        price: Decimal,
        observed_at: datetime,
    ) -> None:
        if self.historical_bar_store is None or price <= Decimal("0"):
            return
        bucket_start = _bar_bucket_start(observed_at, self.bar_timeframe)
        bucket_end = bucket_start + _timeframe_delta(self.bar_timeframe)
        existing = list(self.historical_bar_store.load(market, symbol, self.bar_timeframe, limit=1))
        if existing and existing[-1].opened_at == bucket_start:
            previous = existing[-1]
            bar = HistoricalBar(
                market=market,
                symbol=symbol,
                timeframe=self.bar_timeframe,
                open_price=previous.open_price,
                high_price=max(previous.high_price, price),
                low_price=min(previous.low_price, price),
                close_price=price,
                volume=previous.volume,
                opened_at=bucket_start,
                closed_at=bucket_end,
            )
        else:
            bar = HistoricalBar(
                market=market,
                symbol=symbol,
                timeframe=self.bar_timeframe,
                open_price=price,
                high_price=price,
                low_price=price,
                close_price=price,
                volume=Decimal("0"),
                opened_at=bucket_start,
                closed_at=bucket_end,
            )
        self.historical_bar_store.save((bar,))

    def _bars_for(
        self,
        market: Market,
        symbol: str,
        *,
        limit: int,
    ) -> tuple[HistoricalBar, ...]:
        if self.historical_bar_store is None:
            return ()
        return self.historical_bar_store.load(market, symbol, self.bar_timeframe, limit=limit)

    def _recent_contiguous_bars(
        self,
        bars: tuple[HistoricalBar, ...],
    ) -> tuple[HistoricalBar, ...]:
        if len(bars) < 2:
            return bars

        max_gap = _timeframe_delta(self.bar_timeframe) * 2
        start_index = len(bars) - 1
        for index in range(len(bars) - 1, 0, -1):
            if bars[index].opened_at - bars[index - 1].opened_at > max_gap:
                break
            start_index = index - 1
        return bars[start_index:]

    def _recent_bars_for(
        self,
        market: Market,
        symbol: str,
        *,
        limit: int,
    ) -> tuple[HistoricalBar, ...]:
        return self._recent_contiguous_bars(self._bars_for(market, symbol, limit=limit))

    def _hydrate_price_history_from_bars(
        self,
        market: Market,
        symbol: str,
        bars: tuple[HistoricalBar, ...],
    ) -> None:
        if not bars:
            return
        history = self._history_for(market, symbol)
        history_maxlen = history.maxlen
        history.clear()
        relevant_bars = bars[-history_maxlen:] if history_maxlen is not None else bars
        for bar in relevant_bars:
            history.append(bar.close_price)

    def _rank_symbols(
        self,
        market: Market,
        symbols: tuple[str, ...],
        latest_prices: dict[str, Decimal],
    ) -> list[_RankedSymbol]:
        ranked: list[_RankedSymbol] = []
        for symbol in symbols:
            price = latest_prices.get(symbol)
            if price is None or price <= Decimal("0"):
                continue
            bars = self._recent_bars_for(market, symbol, limit=12)
            recent_prices = [bar.close_price for bar in bars] if bars else list(self._history_for(market, symbol))
            if recent_prices and recent_prices[-1] != price:
                recent_prices = recent_prices[-11:] + [price]
            if len(recent_prices) < 2:
                momentum_ratio = Decimal("0")
            else:
                baseline = recent_prices[0]
                momentum_ratio = (recent_prices[-1] - baseline) / baseline if baseline > Decimal("0") else Decimal("0")
            highs = [bar.high_price for bar in bars] if bars else recent_prices
            lows = [bar.low_price for bar in bars] if bars else recent_prices
            volatility_ratio = (
                (max(highs) - min(lows)) / price if highs and lows and price > Decimal("0") else Decimal("0")
            )
            volumes = [bar.volume for bar in bars if bar.volume > Decimal("0")]
            average_volume = (
                sum(volumes[:-1], Decimal("0")) / Decimal(len(volumes) - 1)
                if len(volumes) > 1
                else Decimal("0")
            )
            volume_ratio = (
                volumes[-1] / average_volume
                if volumes and average_volume > Decimal("0")
                else Decimal("1")
            )
            score = (
                abs(momentum_ratio) * Decimal("4")
                + volatility_ratio * Decimal("2")
                + max(volume_ratio - Decimal("1"), Decimal("0"))
                + (Decimal("0.05") if len(recent_prices) >= 3 else Decimal("0"))
            )
            ranked.append(
                _RankedSymbol(
                    symbol=symbol,
                    score=score,
                    latest_price=price,
                    momentum_ratio=momentum_ratio,
                    volatility_ratio=volatility_ratio,
                    volume_ratio=volume_ratio,
                    bar_count=len(bars),
                )
            )
        ranked.sort(key=lambda item: (item.score, abs(item.momentum_ratio), item.symbol), reverse=True)
        return ranked

    def _market_summary_payload(
        self,
        market: Market,
        activity: ScannerActivity,
        rankings: tuple[_RankedSymbol, ...],
    ) -> dict[str, object]:
        worker = self.workers.get(market)
        worker_symbols = tuple(worker.settings.symbols) if worker is not None else ()
        strategy_id, profile_id = self.selection_provider(market)
        strategy_id = normalize_strategy_id(market, strategy_id)
        profile_id = normalize_profile_id(market, profile_id)
        series = []
        for ranked in rankings[: min(3, self.ranking_top_n)]:
            bars = self._recent_bars_for(market, ranked.symbol, limit=16)
            if bars:
                points = [
                    {
                        "label": bar.opened_at.strftime("%H:%M") if bar.opened_at.date() == datetime.now(UTC).date() else bar.opened_at.strftime("%m-%d"),
                        "value": str(bar.close_price),
                    }
                    for bar in bars
                ]
            else:
                points = [
                    {"label": str(index + 1), "value": str(price)}
                    for index, price in enumerate(self._history_for(market, ranked.symbol))
                ]
            series.append(
                {
                    "market": market.value,
                    "symbol": ranked.symbol,
                    "timeframe": self.bar_timeframe.value,
                    "points": points,
                }
            )
        candles_by_symbol: dict[str, list[dict[str, object]]] = {}
        position_overlays_by_symbol = self._position_overlays_payload(market, worker_symbols, strategy_id, profile_id)
        for symbol in worker_symbols:
            bars = self._recent_bars_for(market, symbol, limit=24)
            if not bars:
                continue
            candles_by_symbol[symbol] = [
                {
                    "label": bar.opened_at.strftime("%H:%M") if bar.opened_at.date() == datetime.now(UTC).date() else bar.opened_at.strftime("%m-%d %H:%M"),
                    "opened_at": bar.opened_at.isoformat(),
                    "open": str(bar.open_price),
                    "high": str(bar.high_price),
                    "low": str(bar.low_price),
                    "close": str(bar.close_price),
                    "volume": str(bar.volume),
                }
                for bar in bars
            ]
        market_events = tuple(self._events[market])
        recent_brakes = sum(1 for event in market_events if event.event_type in {"risk-rejected", "execution-blocked"})
        recent_execution_blocks = sum(1 for event in market_events if event.event_type == "execution-blocked")
        recent_risk_rejections = sum(1 for event in market_events if event.event_type == "risk-rejected")
        recent_orders = sum(1 for event in market_events if event.event_type == "order-submitted")
        recent_signals = sum(
            1 for event in market_events if event.event_type in {"order-submitted", "risk-rejected", "execution-blocked"}
        )
        return {
            "market": market.value,
            "label": _market_display_label(market),
            "warmup_status": activity.warmup_status,
            "last_scan_at": activity.last_scan_at.isoformat() if activity.last_scan_at else None,
            "last_decision": activity.last_decision,
            "candidate_count": activity.candidate_count,
            "candidate_score": activity.candidate_score,
            "selected_candidate": activity.last_selected_candidate,
            "selected_thesis": activity.last_selected_thesis,
            "considered_candidates": list(activity.considered_candidates),
            "top_symbol": rankings[0].symbol if rankings else None,
            "available_symbols": list(worker_symbols),
            "recent_event_count": len(market_events),
            "recent_brake_count": recent_brakes,
            "recent_execution_block_count": recent_execution_blocks,
            "recent_risk_rejection_count": recent_risk_rejections,
            "recent_order_count": recent_orders,
            "recent_signal_count": recent_signals,
            "ranked_symbols": [
                {
                    "symbol": item.symbol,
                    "score": str(item.score.quantize(Decimal('0.0001'))),
                    "latest_price": str(item.latest_price),
                    "momentum_ratio": str(item.momentum_ratio),
                    "volatility_ratio": str(item.volatility_ratio),
                    "volume_ratio": str(item.volume_ratio),
                    "bar_count": item.bar_count,
                }
                for item in rankings[: self.ranking_top_n]
            ],
            "series": series,
            "candles_by_symbol": candles_by_symbol,
            "position_overlays_by_symbol": position_overlays_by_symbol,
            "candle_timeframe": self.bar_timeframe.value,
        }

    def _analytics_payload(
        self,
        events: list[ScannerEvent],
        market_summaries: list[dict[str, object]],
        activities: dict[Market, ScannerActivity],
    ) -> dict[str, object]:
        event_counts = Counter(event.event_type for event in events)
        strategy_counts: Counter[str] = Counter()
        regime_counts: Counter[str] = Counter()
        lifecycle_counts: Counter[str] = Counter()
        brake_reason_counts: Counter[str] = Counter()
        drift_flags: list[dict[str, object]] = []

        for event in events:
            selected_candidate = event.selected_candidate if isinstance(event.selected_candidate, dict) else None
            if selected_candidate is not None:
                strategy_id = _coerce_optional_str(selected_candidate.get("strategy_id"))
                regime = _coerce_optional_str(selected_candidate.get("regime"))
                if strategy_id is not None:
                    strategy_counts[strategy_id] += 1
                if regime is not None:
                    regime_counts[regime] += 1
            selected_thesis = event.selected_thesis if isinstance(event.selected_thesis, dict) else None
            if selected_thesis is not None:
                lifecycle_state = _coerce_optional_str(selected_thesis.get("lifecycle_state"))
                if lifecycle_state is not None:
                    lifecycle_counts[lifecycle_state] += 1
            for detail in event.details:
                if detail.startswith("portfolio_control="):
                    brake_reason_counts[detail.removeprefix("portfolio_control=")] += 1
                elif detail.startswith("execution_quality="):
                    brake_reason_counts[detail.removeprefix("execution_quality=")] += 1

        market_rollups: list[dict[str, object]] = []
        for summary in market_summaries:
            market_name = str(summary.get("market") or "")
            activity = activities[Market(market_name)] if market_name else None
            market_events = [event for event in events if event.market.value == market_name]
            selected_candidate_summary = (
                cast(dict[str, object], summary.get("selected_candidate"))
                if isinstance(summary.get("selected_candidate"), dict)
                else None
            )
            selected_thesis_summary = (
                cast(dict[str, object], summary.get("selected_thesis"))
                if isinstance(summary.get("selected_thesis"), dict)
                else None
            )
            recent_strategy_counts: Counter[str] = Counter()
            recent_regime_counts: Counter[str] = Counter()
            for event in market_events:
                if isinstance(event.selected_candidate, dict):
                    recent_strategy = _coerce_optional_str(event.selected_candidate.get("strategy_id"))
                    recent_regime = _coerce_optional_str(event.selected_candidate.get("regime"))
                    if recent_strategy is not None:
                        recent_strategy_counts[recent_strategy] += 1
                    if recent_regime is not None:
                        recent_regime_counts[recent_regime] += 1
            dominant_recent_strategy = recent_strategy_counts.most_common(1)[0][0] if recent_strategy_counts else None
            dominant_recent_regime = recent_regime_counts.most_common(1)[0][0] if recent_regime_counts else None
            drift_state = "aligned"
            drift_reason = "Current selected thesis aligns with recent scanner flow."
            recent_brake_count = _coerce_int(summary.get("recent_brake_count", 0))
            recent_order_count = _coerce_int(summary.get("recent_order_count", 0))
            top_strategy = (
                _coerce_optional_str(selected_candidate_summary.get("strategy_id"))
                if selected_candidate_summary is not None
                else None
            )
            top_regime = (
                _coerce_optional_str(selected_candidate_summary.get("regime"))
                if selected_candidate_summary is not None
                else None
            )
            if _coerce_int(summary.get("recent_event_count", 0)) == 0:
                drift_state = "quiet"
                drift_reason = "No recent scanner events for this market yet."
            elif recent_brake_count >= 2 and recent_order_count == 0:
                drift_state = "constrained"
                drift_reason = "Recent portfolio or execution brakes are blocking current opportunities."
            elif top_regime is not None and dominant_recent_regime is not None and top_regime != dominant_recent_regime:
                drift_state = "regime-shift"
                drift_reason = f"Current regime {top_regime} differs from recent dominant regime {dominant_recent_regime}."
            elif top_strategy is not None and dominant_recent_strategy is not None and top_strategy != dominant_recent_strategy:
                drift_state = "strategy-rotation"
                drift_reason = f"Current strategy {top_strategy} differs from recent dominant strategy {dominant_recent_strategy}."

            if drift_state not in {"aligned", "quiet"}:
                drift_flags.append(
                    {
                        "market": market_name,
                        "state": drift_state,
                        "reason": drift_reason,
                    }
                )

            market_rollups.append(
                {
                    "market": market_name,
                    "label": summary.get("label"),
                    "warmup_status": summary.get("warmup_status"),
                    "scanner_running": activity.scanner_running if activity is not None else False,
                    "execution_mode": activity.execution_mode if activity is not None else None,
                    "signals_seen": activity.signals_seen if activity is not None else 0,
                    "orders_submitted": activity.orders_submitted if activity is not None else 0,
                    "candidate_count": summary.get("candidate_count", 0),
                    "recent_event_count": summary.get("recent_event_count", 0),
                    "recent_brake_count": recent_brake_count,
                    "recent_execution_block_count": summary.get("recent_execution_block_count", 0),
                    "recent_risk_rejection_count": summary.get("recent_risk_rejection_count", 0),
                    "recent_order_count": recent_order_count,
                    "top_symbol": summary.get("top_symbol"),
                    "top_strategy": top_strategy,
                    "top_regime": top_regime,
                    "dominant_recent_strategy": dominant_recent_strategy,
                    "dominant_recent_regime": dominant_recent_regime,
                    "drift_state": drift_state,
                    "drift_reason": drift_reason,
                    "lifecycle_state": (
                        _coerce_optional_str(selected_thesis_summary.get("lifecycle_state"))
                        if selected_thesis_summary is not None
                        else None
                    ),
                    "last_decision": summary.get("last_decision"),
                }
            )

        return {
            "recent_event_count": len(events),
            "active_scanner_count": sum(1 for activity in activities.values() if activity.scanner_running),
            "ready_market_count": sum(1 for activity in activities.values() if activity.warmup_status == "ready"),
            "signal_count": sum(activity.signals_seen for activity in activities.values()),
            "order_count": sum(activity.orders_submitted for activity in activities.values()),
            "brake_count": event_counts.get("risk-rejected", 0) + event_counts.get("execution-blocked", 0),
            "event_counts": dict(sorted(event_counts.items())),
            "strategy_mix": [
                {"name": name, "count": count}
                for name, count in strategy_counts.most_common(5)
            ],
            "regime_mix": [
                {"name": name, "count": count}
                for name, count in regime_counts.most_common(5)
            ],
            "lifecycle_mix": [
                {"name": name, "count": count}
                for name, count in lifecycle_counts.most_common(5)
            ],
            "brake_reasons": [
                {"name": name, "count": count}
                for name, count in brake_reason_counts.most_common(5)
            ],
            "learning_summary": self._learning_state.analytics_payload(),
            "drift_flags": drift_flags,
            "market_rollups": market_rollups,
        }

    def _position_overlays_payload(
        self,
        market: Market,
        symbols: tuple[str, ...],
        strategy_id: str,
        profile_id: str,
    ) -> dict[str, list[dict[str, object]]]:
        snapshot = self.portfolio_store.load_all().get(market)
        if snapshot is None:
            return {}

        symbol_filter = {symbol.upper() for symbol in symbols}
        overlays: dict[str, list[dict[str, object]]] = {}
        for position in snapshot.positions:
            if position.symbol.upper() not in symbol_filter:
                continue
            overlay = self._position_overlay(market, position, strategy_id, profile_id)
            overlays.setdefault(position.symbol, []).append(
                {
                    "side": overlay.side,
                    "entry_price": str(overlay.entry_price),
                    "close_target_price": str(overlay.close_target_price) if overlay.close_target_price is not None else None,
                    "thesis_id": overlay.thesis_id,
                    "strategy_id": overlay.strategy_id,
                }
            )
        return overlays

    def _position_overlay(
        self,
        market: Market,
        position: NormalizedPosition,
        strategy_id: str,
        profile_id: str,
    ) -> _PositionOverlay:
        selected_thesis = self.selected_thesis_for(market, position.symbol)
        overlay_strategy_id = str(selected_thesis.get("strategy_id")) if isinstance(selected_thesis, dict) and selected_thesis.get("strategy_id") else strategy_id
        overlay_profile_id = str(selected_thesis.get("profile_id")) if isinstance(selected_thesis, dict) and selected_thesis.get("profile_id") else profile_id
        return _PositionOverlay(
            symbol=position.symbol,
            side="buy" if position.quantity >= 0 else "sell",
            entry_price=position.average_price,
            close_target_price=self._planned_exit_price(market, position, overlay_strategy_id, overlay_profile_id),
            thesis_id=str(selected_thesis.get("thesis_id")) if isinstance(selected_thesis, dict) else None,
            strategy_id=overlay_strategy_id,
        )

    def _planned_exit_price(
        self,
        market: Market,
        position: NormalizedPosition,
        strategy_id: str,
        profile_id: str,
    ) -> Decimal | None:
        recent_bars = self._recent_bars_for(market, position.symbol, limit=12)
        recent_prices = tuple(self._history_for(market, position.symbol))
        if len(recent_bars) < 3 and len(recent_prices) < 3:
            return None

        plugin = RollingSignalPlugin(
            profile=_strategy_profile(market, strategy_id, profile_id),
            profile_id=profile_id,
            symbol=position.symbol,
            recent_prices=recent_prices,
            recent_bars=recent_bars,
            quantity=abs(position.quantity),
            allow_short=policy_for_market(market).allow_short,
        )
        return plugin.planned_exit_price(
            StrategyContext(
                market=market,
                account=NormalizedAccount(
                    account_id=f"overlay-{market.value}",
                    currency="USD",
                    equity=Decimal("0"),
                    buying_power=Decimal("0"),
                    cash=Decimal("0"),
                ),
                positions=(position,),
                latest_price=position.market_price,
                bar_timestamp=datetime.now(UTC),
            )
        )

    def _sync_position_open_times(
        self,
        market: Market,
        positions: tuple[NormalizedPosition, ...],
        observed_at: datetime,
    ) -> None:
        active_keys = {(market, position.symbol.upper()) for position in positions}
        with self._lock:
            for key in active_keys:
                self._position_opened_at.setdefault(key, observed_at)
            stale_keys = [key for key in self._position_opened_at if key[0] == market and key not in active_keys]
            for key in stale_keys:
                self._position_opened_at.pop(key, None)

    def _sync_trade_thesis_state(
        self,
        market: Market,
        positions: tuple[NormalizedPosition, ...],
        closed_trades,
    ) -> None:
        active_symbols = {position.symbol.upper() for position in positions}
        persisted_theses = (
            self.operator_state_service.list_active_trade_theses(market)
            if self.operator_state_service is not None
            else {}
        )
        if self.operator_state_service is not None:
            for trade in sorted(tuple(closed_trades), key=lambda item: item.closed_at, reverse=True):
                if self.operator_state_service.find_closed_trade_thesis(
                    market,
                    trade_id=trade.trade_id,
                    symbol=trade.symbol,
                    opened_at=trade.opened_at,
                    closed_at=trade.closed_at,
                ) is not None:
                    continue
                symbol = trade.symbol.upper()
                if symbol not in active_symbols:
                    continue
                thesis = persisted_theses.get(symbol)
                if thesis is None:
                    with self._lock:
                        thesis = self._selected_theses.get((market, symbol))
                if thesis is None:
                    continue
                thesis_created_at = _coerce_datetime(thesis.get("created_at"))
                if thesis_created_at is not None and trade.closed_at < thesis_created_at:
                    continue
                scale_out_count = _coerce_int(thesis.get("scale_out_count", 0))
                archived_scale_out_count = _coerce_int(thesis.get("archived_scale_out_count", 0))
                if scale_out_count <= archived_scale_out_count:
                    continue
                self.operator_state_service.archive_closed_trade_thesis(
                    market,
                    trade.trade_id,
                    thesis,
                    trade_symbol=trade.symbol,
                    opened_at=trade.opened_at,
                    closed_at=trade.closed_at,
                    state="scaled-out",
                    reason=str(thesis.get("lifecycle_reason") or "partial scale-out executed"),
                    transitioned_at=trade.closed_at,
                )
                self._learning_state.record_closed_trade(market=market, trade=trade, thesis=thesis)
                updated_thesis = dict(thesis)
                updated_thesis["archived_scale_out_count"] = archived_scale_out_count + 1
                self.operator_state_service.upsert_active_trade_thesis(market, symbol, updated_thesis)
                persisted_theses[symbol] = updated_thesis
                with self._lock:
                    self._selected_theses[(market, symbol)] = updated_thesis
        with self._lock:
            in_memory_stale = {
                symbol
                for thesis_market, symbol in self._selected_theses
                if thesis_market == market and symbol not in active_symbols
            }
        stale_symbols = sorted(in_memory_stale.union(symbol for symbol in persisted_theses if symbol not in active_symbols))

        for symbol in stale_symbols:
            thesis = persisted_theses.get(symbol)
            if thesis is None:
                with self._lock:
                    thesis = self._selected_theses.get((market, symbol))
            closed_trade = next(
                (
                    trade
                    for trade in reversed(tuple(closed_trades))
                    if trade.symbol.upper() == symbol
                    and (
                        self.operator_state_service is None
                        or self.operator_state_service.find_closed_trade_thesis(
                            market,
                            trade_id=trade.trade_id,
                            symbol=trade.symbol,
                            opened_at=trade.opened_at,
                            closed_at=trade.closed_at,
                        ) is None
                    )
                ),
                None,
            )
            if closed_trade is not None and thesis is not None and self.operator_state_service is not None:
                self.operator_state_service.archive_closed_trade_thesis(
                    market,
                    closed_trade.trade_id,
                    thesis,
                    trade_symbol=closed_trade.symbol,
                    opened_at=closed_trade.opened_at,
                    closed_at=closed_trade.closed_at,
                    state="closed",
                    reason=str(thesis.get("lifecycle_reason") or "position-closed"),
                    transitioned_at=closed_trade.closed_at,
                )
                self._learning_state.record_closed_trade(market=market, trade=closed_trade, thesis=thesis)
            if self.operator_state_service is not None:
                self.operator_state_service.remove_active_trade_thesis(market, symbol)
            with self._lock:
                self._selected_theses.pop((market, symbol), None)

    def _set_activity_locked(self, market: Market, **changes: object) -> None:
        current = self._activities[market]
        self._activities[market] = ScannerActivity(
            market=market,
            automation_state=str(changes.get("automation_state", current.automation_state)),
            execution_mode=str(changes.get("execution_mode", current.execution_mode)),
            scanner_running=bool(changes.get("scanner_running", current.scanner_running)),
            warmup_status=str(changes.get("warmup_status", current.warmup_status)),
            signals_seen=_coerce_int(changes.get("signals_seen", current.signals_seen)),
            orders_submitted=_coerce_int(changes.get("orders_submitted", current.orders_submitted)),
            last_scan_at=_coerce_datetime(changes.get("last_scan_at", current.last_scan_at)),
            last_signal_at=_coerce_datetime(changes.get("last_signal_at", current.last_signal_at)),
            last_order_at=_coerce_datetime(changes.get("last_order_at", current.last_order_at)),
            warmup_completed_at=_coerce_datetime(changes.get("warmup_completed_at", current.warmup_completed_at)),
            last_decision=str(changes.get("last_decision", current.last_decision)),
            last_signal_symbol=_coerce_optional_str(changes.get("last_signal_symbol", current.last_signal_symbol)),
            last_order_id=_coerce_optional_str(changes.get("last_order_id", current.last_order_id)),
            last_error=_coerce_optional_str(changes.get("last_error", current.last_error)),
            last_price=_coerce_optional_str(changes.get("last_price", current.last_price)),
            candidate_count=_coerce_int(changes.get("candidate_count", current.candidate_count)),
            candidate_score=_coerce_optional_str(changes.get("candidate_score", current.candidate_score)),
            last_selected_candidate=_coerce_optional_dict(changes.get("last_selected_candidate", current.last_selected_candidate)),
            last_selected_thesis=_coerce_optional_dict(changes.get("last_selected_thesis", current.last_selected_thesis)),
            considered_candidates=_coerce_dict_sequence(changes.get("considered_candidates", current.considered_candidates)),
        )

    def _record_event_locked(
        self,
        market: Market,
        *,
        message: str,
        event_type: str,
        occurred_at: datetime,
        level: str = "info",
        symbol: str | None = None,
        strategy_id: str | None = None,
        profile_id: str | None = None,
        price: str | None = None,
        details: tuple[str, ...] | list[str] = (),
        candidate_count: int = 0,
        candidate_score: str | None = None,
        selected_candidate: dict[str, object] | None = None,
        selected_thesis: dict[str, object] | None = None,
        considered_candidates: tuple[dict[str, object], ...] | list[dict[str, object]] = (),
    ) -> None:
        self._events[market].appendleft(
            ScannerEvent(
                market=market,
                message=message,
                occurred_at=occurred_at,
                event_type=event_type,
                level=level,
                symbol=symbol,
                strategy_id=strategy_id,
                profile_id=profile_id,
                price=price,
                details=tuple(details),
                candidate_count=candidate_count,
                candidate_score=candidate_score,
                selected_candidate=selected_candidate,
                selected_thesis=selected_thesis,
                considered_candidates=tuple(considered_candidates),
            )
        )
def _coerce_int(value: object) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value)
    return 0


def _coerce_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        return datetime.fromisoformat(value)
    return None


def _coerce_optional_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _coerce_optional_dict(value: object) -> dict[str, object] | None:
    return value if isinstance(value, dict) else None


def _coerce_dict_sequence(value: object) -> tuple[dict[str, object], ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(item for item in value if isinstance(item, dict))


def _recommended_quote_ttl_seconds(worker: MarketWorker) -> int:
    adapter_name = worker.adapter.metadata().adapter_name
    if adapter_name == "ig-forex-au":
        return 60
    if adapter_name == "alpaca":
        return 20
    if adapter_name == "binance":
        return 12
    return 8


def _market_display_label(market: Market) -> str:
    return {
        Market.STOCKS: "Stocks - Alpaca",
        Market.CRYPTO: "Crypto - Binance",
        Market.FOREX: "Forex - IG Forex AU",
    }.get(market, market.value.title())


def _bar_bucket_start(observed_at: datetime, timeframe: BarTimeframe) -> datetime:
    if timeframe == BarTimeframe.ONE_MINUTE:
        return observed_at.replace(second=0, microsecond=0)
    if timeframe == BarTimeframe.FIVE_MINUTES:
        minute = (observed_at.minute // 5) * 5
        return observed_at.replace(minute=minute, second=0, microsecond=0)
    if timeframe == BarTimeframe.FIFTEEN_MINUTES:
        minute = (observed_at.minute // 15) * 15
        return observed_at.replace(minute=minute, second=0, microsecond=0)
    if timeframe == BarTimeframe.ONE_HOUR:
        return observed_at.replace(minute=0, second=0, microsecond=0)
    return observed_at.replace(hour=0, minute=0, second=0, microsecond=0)


def _timeframe_delta(timeframe: BarTimeframe) -> timedelta:
    if timeframe == BarTimeframe.ONE_MINUTE:
        return timedelta(minutes=1)
    if timeframe == BarTimeframe.FIVE_MINUTES:
        return timedelta(minutes=5)
    if timeframe == BarTimeframe.FIFTEEN_MINUTES:
        return timedelta(minutes=15)
    if timeframe == BarTimeframe.ONE_HOUR:
        return timedelta(hours=1)
    return timedelta(days=1)

