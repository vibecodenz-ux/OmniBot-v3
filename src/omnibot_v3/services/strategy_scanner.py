"""Background strategy scanning and optional order execution for runtime markets."""

from __future__ import annotations

import threading
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import ROUND_DOWN, Decimal
from typing import Protocol, TypedDict, cast

from omnibot_v3.domain import (
    BarTimeframe,
    HistoricalBar,
    Market,
    NormalizedAccount,
    NormalizedPosition,
    OrderRequest,
    OrderSide,
    OrderType,
    RiskPolicy,
    StrategyContext,
    StrategyProfile,
    StrategySignal,
)
from omnibot_v3.services.market_catalog import normalize_profile_id, normalize_strategy_id
from omnibot_v3.services.market_data_store import HistoricalBarStore
from omnibot_v3.services.market_hours import MarketHoursService
from omnibot_v3.services.market_worker import MarketWorker
from omnibot_v3.services.orchestrator import TradingOrchestrator
from omnibot_v3.services.risk_engine import RiskPolicyEngine, StrategyRuntime
from omnibot_v3.services.runtime_store import PortfolioSnapshotStore


class SelectionProvider(Protocol):
    def __call__(self, market: Market) -> tuple[str, str]:
        """Return the selected strategy_id and profile_id for a market."""


class ProfileSettings(TypedDict):
    target_notional: Decimal
    threshold_bias: Decimal
    breakout_buffer: Decimal
    cooldown_seconds: int


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


@dataclass(frozen=True, slots=True)
class _SignalDiagnostics:
    current_price: Decimal
    previous_price: Decimal
    baseline_price: Decimal
    delta_ratio: Decimal
    baseline_ratio: Decimal
    prior_high: Decimal
    prior_low: Decimal
    momentum_up: bool
    momentum_down: bool
    breakout_up: bool
    breakout_down: bool
    reversion_up: bool
    reversion_down: bool
    long_votes: int
    short_votes: int
    momentum_up_delta_min: Decimal
    momentum_up_baseline_min: Decimal
    momentum_down_delta_max: Decimal
    momentum_down_baseline_max: Decimal
    breakout_above_level: Decimal
    breakout_below_level: Decimal
    reversion_up_baseline_max: Decimal
    reversion_up_delta_min: Decimal
    reversion_down_baseline_min: Decimal
    reversion_down_delta_max: Decimal


@dataclass(frozen=True, slots=True)
class _RankedSymbol:
    symbol: str
    score: Decimal
    latest_price: Decimal
    momentum_ratio: Decimal
    volatility_ratio: Decimal
    volume_ratio: Decimal
    bar_count: int


@dataclass(slots=True)
class StrategyScannerService:
    orchestrator: TradingOrchestrator
    workers: dict[Market, MarketWorker]
    portfolio_store: PortfolioSnapshotStore
    selection_provider: SelectionProvider
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
                last_decision="Scanner started and warmup queued.",
                last_error=None,
            )
            self._record_event_locked(
                market,
                message="Scanner started for live strategy evaluation.",
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
            "top_ranked_symbol": self._rankings[market][0].symbol if self._rankings[market] else None,
            "ranked_symbols": [item.symbol for item in self._rankings[market][: self.ranking_top_n]],
        }

    def decision_log_payload(self) -> dict[str, object]:
        with self._lock:
            events = [event for market in (Market.STOCKS, Market.CRYPTO, Market.FOREX) for event in self._events[market]]
            activities = dict(self._activities)
            rankings = dict(self._rankings)
        events.sort(key=lambda item: item.occurred_at, reverse=True)
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
                }
                for event in events[:40]
            ],
            "market_summaries": [
                self._market_summary_payload(market, activities[market], rankings.get(market, ()))
                for market in (Market.STOCKS, Market.CRYPTO, Market.FOREX)
            ],
        }

    def _run_market_loop(self, market: Market, stop_event: threading.Event) -> None:
        worker = self.workers[market]
        while not stop_event.is_set():
            try:
                snapshot = self.orchestrator.snapshot(market)
                if snapshot.state.value == "RUNNING":
                    self._scan_market(market)
                else:
                    self.orchestrator.heartbeat(market)
                    with self._lock:
                        self._set_activity_locked(
                            market,
                            automation_state="connected-only",
                            scanner_running=True,
                        )
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

    def _scan_market(self, market: Market) -> None:
        worker = self.workers[market]
        validation = worker.validate_configuration()
        now = datetime.now(UTC)
        if not validation.valid:
            self.orchestrator.heartbeat(market)
            with self._lock:
                self._set_activity_locked(
                    market,
                    automation_state="awaiting-credentials",
                    scanner_running=True,
                    last_decision="; ".join(validation.errors),
                    last_scan_at=now,
                )
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
                    automation_state="actively-scanning",
                    warmup_status=resolved_warmup_status,
                    scanner_running=True,
                    last_decision=detail,
                    last_scan_at=now,
                )
            return

        snapshot = self._portfolio_snapshot_for_scan(market, worker, now)
        self.portfolio_store.save(snapshot)
        worker_status = worker.status()
        self.orchestrator.heartbeat(market, last_reconciled_at=worker_status.last_reconciled_at)

        strategy_id, profile_id = self.selection_provider(market)
        strategy_id = normalize_strategy_id(market, strategy_id)
        profile_id = normalize_profile_id(market, profile_id)
        execution_mode = _execution_mode_for_profile(profile_id)
        latest_prices = self._prices_for_symbols(worker, market, tuple(worker.settings.symbols), now)
        ranked_symbols = self._rank_symbols(market, tuple(worker.settings.symbols), latest_prices)
        evaluation = self._evaluate_symbols(
            market,
            worker,
            snapshot.account,
            snapshot.positions,
            strategy_id,
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
        }
        if evaluation.get("signal_detected"):
            with self._lock:
                current = self._activities[market]
                activity_updates["signals_seen"] = current.signals_seen + 1
                activity_updates["last_signal_at"] = now

        order_request = evaluation.get("order_request")
        if isinstance(order_request, OrderRequest) and execution_mode == "scan-and-trade":
            try:
                order = worker.submit_order(order_request)
                refreshed_snapshot = worker.reconcile_portfolio()
            except Exception as exc:
                rejected_symbol = str(evaluation.get("signal_symbol") or order_request.symbol)
                activity_updates["last_error"] = str(exc)
                activity_updates["last_decision"] = _execution_summary(rejected_symbol, f"order rejected: {exc}")
                with self._lock:
                    self._record_event_locked(
                        market,
                        message=str(activity_updates["last_decision"]),
                        event_type="order-rejected",
                        occurred_at=now,
                        level="warning",
                        symbol=rejected_symbol,
                        strategy_id=strategy_id,
                        profile_id=profile_id,
                        price=str(evaluation.get("price") or order_request.limit_price or ""),
                    )
            else:
                self.portfolio_store.save(refreshed_snapshot)
                worker_status = worker.status()
                self.orchestrator.heartbeat(market, last_reconciled_at=worker_status.last_reconciled_at)
                with self._lock:
                    current = self._activities[market]
                    activity_updates["orders_submitted"] = current.orders_submitted + 1
                activity_updates["last_order_at"] = now
                activity_updates["last_order_id"] = order.order_id
                activity_updates["last_decision"] = f"Submitted {order.side.value.upper()} {order.symbol} from {strategy_id}."
                with self._lock:
                    self._record_event_locked(
                        market,
                        message=str(activity_updates["last_decision"]),
                        event_type="order-submitted",
                        occurred_at=now,
                        symbol=order.symbol,
                        strategy_id=strategy_id,
                        profile_id=profile_id,
                        price=str(order.average_fill_price or order_request.limit_price or ""),
                    )

        with self._lock:
            self._set_activity_locked(market, **activity_updates)

    def _evaluate_symbols(
        self,
        market: Market,
        worker: MarketWorker,
        account: NormalizedAccount,
        positions: tuple[NormalizedPosition, ...],
        strategy_id: str,
        profile_id: str,
        latest_prices: dict[str, Decimal],
        ranked_symbols: list[_RankedSymbol],
        observed_at: datetime,
    ) -> dict[str, object]:
        ordered_symbols = [item.symbol for item in ranked_symbols] or list(worker.settings.symbols)
        latest_decision = f"Scanning {len(ordered_symbols)} ranked symbols."
        fallback_result: dict[str, object] | None = None
        for symbol in ordered_symbols:
            price = latest_prices.get(symbol)
            if price is None or price <= Decimal("0"):
                latest_decision = f"{symbol}: no quote available."
                with self._lock:
                    self._record_event_locked(
                        market,
                        message=f"{symbol}: broker price unavailable.",
                        event_type="quote-missing",
                        occurred_at=observed_at,
                        level="warning",
                        symbol=symbol,
                        strategy_id=strategy_id,
                        profile_id=profile_id,
                    )
                continue
            self._record_live_bar(market, symbol, price, observed_at)
            history = self._history_for(market, symbol)
            history.append(price)
            bars = self._bars_for(market, symbol, limit=12)
            plugin = _RollingSignalPlugin(
                profile=_strategy_profile(market, strategy_id, profile_id),
                profile_id=profile_id,
                symbol=symbol,
                recent_prices=tuple(history),
                recent_bars=bars,
                quantity=_order_quantity(market, profile_id, price),
                allow_short=market in {Market.CRYPTO, Market.FOREX},
            )
            runtime = StrategyRuntime(
                plugin=plugin,
                risk_engine=_risk_engine(market, profile_id),
            )
            context = StrategyContext(
                market=market,
                account=account,
                positions=positions,
                latest_price=price,
                bar_timestamp=datetime.now(UTC),
            )
            result = runtime.evaluate(context)
            latest_decision = _execution_summary(symbol, result.decision.reason)
            if result.order_request is None:
                detail_lines = plugin.explain_no_signal(context)
                with self._lock:
                    self._record_event_locked(
                        market,
                        message=f"{symbol}: analysed at {price} and skipped because {result.decision.reason}.",
                        event_type="analysis-skip",
                        occurred_at=observed_at,
                        symbol=symbol,
                        strategy_id=strategy_id,
                        profile_id=profile_id,
                        price=str(price),
                        details=detail_lines,
                    )
                fallback_result = {"decision": latest_decision, "price": str(price), "signal_symbol": symbol}
                continue
            if not _can_submit_for_symbol(market, symbol, positions, result.order_request):
                with self._lock:
                    self._record_event_locked(
                        market,
                        message=f"{symbol}: signal generated but execution is disabled for this market.",
                        event_type="execution-blocked",
                        occurred_at=observed_at,
                        level="warning",
                        symbol=symbol,
                        strategy_id=strategy_id,
                        profile_id=profile_id,
                        price=str(price),
                    )
                fallback_result = {
                    "decision": _execution_summary(symbol, "signal generated but execution is scan-only for this market"),
                    "signal_detected": True,
                    "signal_symbol": symbol,
                    "price": str(price),
                }
                continue
            if _in_symbol_cooldown(self._last_trade_at.get((market, symbol)), profile_id):
                with self._lock:
                    self._record_event_locked(
                        market,
                        message=f"{symbol}: signal detected at {price} but cooldown is active.",
                        event_type="cooldown-blocked",
                        occurred_at=observed_at,
                        level="warning",
                        symbol=symbol,
                        strategy_id=strategy_id,
                        profile_id=profile_id,
                        price=str(price),
                    )
                fallback_result = {
                    "decision": _execution_summary(symbol, "signal generated but symbol cooldown is active"),
                    "signal_detected": True,
                    "signal_symbol": symbol,
                    "price": str(price),
                }
                continue
            self._last_trade_at[(market, symbol)] = datetime.now(UTC)
            with self._lock:
                self._record_event_locked(
                    market,
                    message=f"{symbol}: signal accepted at {price} for {result.order_request.side.value.upper()}.",
                    event_type="signal-accepted",
                    occurred_at=observed_at,
                    symbol=symbol,
                    strategy_id=strategy_id,
                    profile_id=profile_id,
                    price=str(price),
                )
            return {
                "decision": _execution_summary(symbol, f"signal accepted for {result.order_request.side.value.upper()}"),
                "signal_detected": True,
                "signal_symbol": symbol,
                "order_request": result.order_request,
                "price": str(price),
            }
        if fallback_result is not None:
            return fallback_result
        return {"decision": latest_decision}

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
    ) -> Decimal | None:
        return self._prices_for_symbols(worker, market, (symbol,), observed_at).get(symbol)

    def _prices_for_symbols(
        self,
        worker: MarketWorker,
        market: Market,
        symbols: tuple[str, ...],
        observed_at: datetime,
    ) -> dict[str, Decimal]:
        resolved: dict[str, Decimal] = {}
        missing_symbols: list[str] = []
        cache_ttl_seconds = self.quote_ttl_seconds
        if cache_ttl_seconds > 0:
            cache_ttl_seconds = max(cache_ttl_seconds, _recommended_quote_ttl_seconds(worker))

        for symbol in symbols:
            cache_key = (market, symbol)
            cached = self._quote_cache.get(cache_key)
            if cached is not None and observed_at - cached[1] <= timedelta(seconds=cache_ttl_seconds):
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
        for symbol in worker.settings.symbols:
            bars = list(self._bars_for(market, symbol, limit=self.warmup_bars))
            refresh_key = (market, symbol)
            last_refresh = self._last_history_refresh_at.get(refresh_key)
            should_refresh = (
                callable(adapter_fetch)
                and (last_refresh is None or observed_at - last_refresh >= timedelta(seconds=self.history_refresh_interval_seconds))
                and len(bars) < self.warmup_bars
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
            bars = self._bars_for(market, symbol, limit=12)
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
        series = []
        for ranked in rankings[: min(3, self.ranking_top_n)]:
            bars = self._bars_for(market, ranked.symbol, limit=16)
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
        for symbol in worker_symbols:
            bars = self._bars_for(market, symbol, limit=24)
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
        return {
            "market": market.value,
            "warmup_status": activity.warmup_status,
            "last_scan_at": activity.last_scan_at.isoformat() if activity.last_scan_at else None,
            "last_decision": activity.last_decision,
            "top_symbol": rankings[0].symbol if rankings else None,
            "available_symbols": list(worker_symbols),
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
            "candle_timeframe": self.bar_timeframe.value,
        }

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
            )
        )


@dataclass(frozen=True, slots=True)
class _RollingSignalPlugin:
    profile: StrategyProfile
    profile_id: str
    symbol: str
    recent_prices: tuple[Decimal, ...]
    quantity: Decimal
    recent_bars: tuple[HistoricalBar, ...] = ()
    allow_short: bool = False

    def generate_signal(self, context: StrategyContext) -> StrategySignal | None:
        if len(self.recent_prices) < 3 or context.latest_price is None:
            return None

        diagnostics = self._signal_diagnostics(context)
        if diagnostics is None:
            return None
        position = next((item for item in context.positions if item.symbol.upper() == self.symbol.upper()), None)
        strategy_id = self.profile.strategy_id

        if strategy_id == "test_drive":
            if position is not None and position.quantity > 0 and diagnostics.current_price <= diagnostics.previous_price:
                return self._signal(OrderSide.SELL, diagnostics.current_price, "test drive exit on any stall or downtick")
            if position is not None and position.quantity < 0 and diagnostics.current_price >= diagnostics.previous_price:
                return self._signal(OrderSide.BUY, diagnostics.current_price, "test drive exit on any stall or uptick")
            if position is not None:
                return None
            if self.allow_short and diagnostics.current_price < diagnostics.previous_price:
                return self._signal(OrderSide.SELL, diagnostics.current_price, "test drive short on minimal downside pressure")
            return self._signal(OrderSide.BUY, diagnostics.current_price, "test drive long on minimal confirmation")

        if position is not None and position.quantity > 0 and diagnostics.baseline_ratio <= Decimal("-0.008"):
            return self._signal(OrderSide.SELL, diagnostics.current_price, "existing long weakened below baseline")
        if position is not None and position.quantity < 0 and diagnostics.baseline_ratio >= Decimal("0.008"):
            return self._signal(OrderSide.BUY, diagnostics.current_price, "existing short weakened above baseline")

        if position is not None:
            return None

        if strategy_id == "momentum":
            if diagnostics.momentum_up:
                return self._signal(OrderSide.BUY, diagnostics.current_price, "momentum confirmation")
            if self.allow_short and diagnostics.momentum_down:
                return self._signal(OrderSide.SELL, diagnostics.current_price, "downside momentum confirmation")

        if strategy_id == "breakout":
            if diagnostics.breakout_up:
                return self._signal(OrderSide.BUY, diagnostics.current_price, "breakout above recent range")
            if self.allow_short and diagnostics.breakout_down:
                return self._signal(OrderSide.SELL, diagnostics.current_price, "breakdown below recent range")

        if strategy_id == "mean_reversion":
            if diagnostics.reversion_up:
                return self._signal(OrderSide.BUY, diagnostics.current_price, "reversion from downside stretch")
            if self.allow_short and diagnostics.reversion_down:
                return self._signal(OrderSide.SELL, diagnostics.current_price, "reversion from upside stretch")

        if strategy_id == "ml_ensemble":
            if diagnostics.long_votes >= 2:
                return self._signal(OrderSide.BUY, diagnostics.current_price, "ensemble confirmed long setup")
            if self.allow_short and diagnostics.short_votes >= 2:
                return self._signal(OrderSide.SELL, diagnostics.current_price, "ensemble confirmed short setup")

        return None

    def explain_no_signal(self, context: StrategyContext) -> tuple[str, ...]:
        if len(self.recent_prices) < 3:
            return (f"history {len(self.recent_prices)}/3 bars collected before evaluation",)
        diagnostics = self._signal_diagnostics(context)
        if diagnostics is None:
            return ("price history is not usable yet",)

        position = next((item for item in context.positions if item.symbol.upper() == self.symbol.upper()), None)
        if self.profile.strategy_id == "test_drive":
            if len(self.recent_prices) < 3:
                return (f"history {len(self.recent_prices)}/3 bars collected before evaluation",)
            if position is not None and position.quantity > 0:
                return (
                    f"test drive long exits when current<=previous; current={diagnostics.current_price:.6f}, previous={diagnostics.previous_price:.6f}",
                )
            if position is not None and position.quantity < 0:
                return (
                    f"test drive short exits when current>=previous; current={diagnostics.current_price:.6f}, previous={diagnostics.previous_price:.6f}",
                )
            if self.allow_short:
                return (
                    f"test drive enters BUY when current>=previous or SELL when current<previous; current={diagnostics.current_price:.6f}, previous={diagnostics.previous_price:.6f}",
                )
            return (
                f"test drive enters BUY once 3 bars exist; current={diagnostics.current_price:.6f}, previous={diagnostics.previous_price:.6f}",
            )

        if position is not None:
            if position.quantity > 0:
                return (
                    f"open long held; exit needs baseline_ratio <= -0.0080, current={diagnostics.baseline_ratio:.4f}",
                )
            return (
                f"open short held; exit needs baseline_ratio >= 0.0080, current={diagnostics.baseline_ratio:.4f}",
            )

        strategy_id = self.profile.strategy_id
        if strategy_id == "momentum":
            details = [
                f"delta_ratio={diagnostics.delta_ratio:.4f} vs buy>={diagnostics.momentum_up_delta_min:.4f}",
                f"baseline_ratio={diagnostics.baseline_ratio:.4f} vs buy>={diagnostics.momentum_up_baseline_min:.4f}",
            ]
            if self.allow_short:
                details.append(
                    f"short gate delta<={diagnostics.momentum_down_delta_max:.4f}, baseline<={diagnostics.momentum_down_baseline_max:.4f}"
                )
            return tuple(details)
        if strategy_id == "breakout":
            details = [
                f"current={diagnostics.current_price:.6f} vs breakout_above={diagnostics.breakout_above_level:.6f}",
            ]
            if self.allow_short:
                details.append(
                    f"current={diagnostics.current_price:.6f} vs breakdown_below={diagnostics.breakout_below_level:.6f}"
                )
            return tuple(details)
        if strategy_id == "mean_reversion":
            details = [
                f"baseline_ratio={diagnostics.baseline_ratio:.4f} vs buy<={diagnostics.reversion_up_baseline_max:.4f}",
                f"delta_ratio={diagnostics.delta_ratio:.4f} vs buy>={diagnostics.reversion_up_delta_min:.4f}",
            ]
            if self.allow_short:
                details.append(
                    f"short gate baseline>={diagnostics.reversion_down_baseline_min:.4f}, delta<={diagnostics.reversion_down_delta_max:.4f}"
                )
            return tuple(details)
        if strategy_id == "ml_ensemble":
            return (
                f"ensemble votes long={diagnostics.long_votes}/2 short={diagnostics.short_votes}/2",
                f"momentum={_yes_no(diagnostics.momentum_up or diagnostics.momentum_down)} breakout={_yes_no(diagnostics.breakout_up or diagnostics.breakout_down)} reversion={_yes_no(diagnostics.reversion_up or diagnostics.reversion_down)}",
            )
        return (
            f"delta_ratio={diagnostics.delta_ratio:.4f}",
            f"baseline_ratio={diagnostics.baseline_ratio:.4f}",
        )

    def _signal_diagnostics(self, context: StrategyContext) -> _SignalDiagnostics | None:
        del context
        profile_settings = _profile_settings(self.profile_id)
        if len(self.recent_bars) >= 3:
            current_price = self.recent_bars[-1].close_price
            previous_price = self.recent_bars[-2].close_price
            baseline_price = sum((bar.close_price for bar in self.recent_bars[:-1]), Decimal("0")) / Decimal(len(self.recent_bars) - 1)
            prior_high = max(bar.high_price for bar in self.recent_bars[:-1])
            prior_low = min(bar.low_price for bar in self.recent_bars[:-1])
        else:
            current_price = self.recent_prices[-1]
            previous_price = self.recent_prices[-2]
            baseline_price = sum(self.recent_prices[:-1], Decimal("0")) / Decimal(len(self.recent_prices) - 1)
            prior_high = max(self.recent_prices[:-1])
            prior_low = min(self.recent_prices[:-1])
        if previous_price <= Decimal("0") or baseline_price <= Decimal("0"):
            return None
        delta_ratio = (current_price - previous_price) / previous_price
        baseline_ratio = (current_price - baseline_price) / baseline_price
        momentum_up_delta_min = Decimal("0.003") + profile_settings["threshold_bias"]
        momentum_up_baseline_min = Decimal("0.004") + profile_settings["threshold_bias"]
        momentum_down_delta_max = Decimal("-0.004") - profile_settings["threshold_bias"]
        momentum_down_baseline_max = Decimal("-0.005") - profile_settings["threshold_bias"]
        breakout_above_level = prior_high * (Decimal("1.002") - profile_settings["breakout_buffer"])
        breakout_below_level = prior_low * (Decimal("0.998") + profile_settings["breakout_buffer"])
        reversion_up_baseline_max = Decimal("-0.007") - profile_settings["threshold_bias"]
        reversion_up_delta_min = Decimal("0.002") - profile_settings["threshold_bias"]
        reversion_down_baseline_min = Decimal("0.007") + profile_settings["threshold_bias"]
        reversion_down_delta_max = Decimal("-0.002") + profile_settings["threshold_bias"]
        momentum_up = delta_ratio >= momentum_up_delta_min and baseline_ratio >= momentum_up_baseline_min
        momentum_down = delta_ratio <= momentum_down_delta_max and baseline_ratio <= momentum_down_baseline_max
        breakout_up = current_price >= breakout_above_level
        breakout_down = current_price <= breakout_below_level
        reversion_up = baseline_ratio <= reversion_up_baseline_max and delta_ratio >= reversion_up_delta_min
        reversion_down = baseline_ratio >= reversion_down_baseline_min and delta_ratio <= reversion_down_delta_max
        return _SignalDiagnostics(
            current_price=current_price,
            previous_price=previous_price,
            baseline_price=baseline_price,
            delta_ratio=delta_ratio,
            baseline_ratio=baseline_ratio,
            prior_high=prior_high,
            prior_low=prior_low,
            momentum_up=momentum_up,
            momentum_down=momentum_down,
            breakout_up=breakout_up,
            breakout_down=breakout_down,
            reversion_up=reversion_up,
            reversion_down=reversion_down,
            long_votes=sum((momentum_up, breakout_up, reversion_up)),
            short_votes=sum((momentum_down, breakout_down, reversion_down)),
            momentum_up_delta_min=momentum_up_delta_min,
            momentum_up_baseline_min=momentum_up_baseline_min,
            momentum_down_delta_max=momentum_down_delta_max,
            momentum_down_baseline_max=momentum_down_baseline_max,
            breakout_above_level=breakout_above_level,
            breakout_below_level=breakout_below_level,
            reversion_up_baseline_max=reversion_up_baseline_max,
            reversion_up_delta_min=reversion_up_delta_min,
            reversion_down_baseline_min=reversion_down_baseline_min,
            reversion_down_delta_max=reversion_down_delta_max,
        )

    def _signal(self, side: OrderSide, reference_price: Decimal, rationale: str) -> StrategySignal:
        return StrategySignal(
            strategy_id=self.profile.strategy_id,
            order_request=OrderRequest(
                client_order_id=_client_order_id(self.profile.market, self.symbol, self.profile.strategy_id),
                symbol=self.symbol,
                side=side,
                quantity=self.quantity,
                order_type=OrderType.MARKET,
                limit_price=reference_price,
            ),
            rationale=rationale,
        )


def _risk_engine(market: Market, profile_id: str) -> RiskPolicyEngine:
    max_order_notional = _profile_settings(profile_id)["target_notional"]
    return RiskPolicyEngine(
        policy=RiskPolicy(
            max_order_notional=max_order_notional,
            max_position_notional=max_order_notional * Decimal("3"),
            max_daily_loss=max_order_notional,
            max_drawdown=max_order_notional * Decimal("2"),
        )
    )


def _strategy_profile(market: Market, strategy_id: str, profile_id: str) -> StrategyProfile:
    return StrategyProfile(
        strategy_id=strategy_id,
        name=strategy_id.replace("-", " ").title(),
        version="scan-v2-7-aligned",
        market=market,
        description=f"{strategy_id} using profile {profile_id}",
        tags=(profile_id,),
        enabled=True,
    )


def _order_quantity(market: Market, profile_id: str, price: Decimal) -> Decimal:
    if price <= Decimal("0"):
        return Decimal("0")
    target_notional = _profile_settings(profile_id)["target_notional"]
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


def _execution_mode_for_profile(profile_id: str) -> str:
    del profile_id
    return "scan-and-trade"


def _client_order_id(market: Market, symbol: str, strategy_id: str) -> str:
    timestamp = int(datetime.now(UTC).timestamp())
    market_code = market.value[:2]
    safe_symbol = "".join(character for character in symbol.lower() if character.isalnum())[:8] or "symbol"
    strategy_code = "".join(character for character in strategy_id.lower() if character.isalnum())[:4] or "strt"
    return f"sc-{market_code}-{safe_symbol}-{strategy_code}-{timestamp}"


def _execution_summary(symbol: str, message: str) -> str:
    return f"{symbol}: {message}"


def _yes_no(value: bool) -> str:
    return "yes" if value else "no"


def _can_submit_for_symbol(
    market: Market,
    symbol: str,
    positions: tuple[NormalizedPosition, ...],
    order_request: OrderRequest,
) -> bool:
    del market, symbol, positions, order_request
    return True


def _in_symbol_cooldown(last_trade_at: datetime | None, profile_id: str) -> bool:
    if last_trade_at is None:
        return False
    return (datetime.now(UTC) - last_trade_at).total_seconds() < _profile_settings(profile_id)["cooldown_seconds"]


def _profile_settings(profile_id: str) -> ProfileSettings:
    profiles: dict[str, ProfileSettings] = {
        "conservative": {
            "target_notional": Decimal("350"),
            "threshold_bias": Decimal("0.0015"),
            "breakout_buffer": Decimal("0.0006"),
            "cooldown_seconds": 420,
        },
        "moderate": {
            "target_notional": Decimal("650"),
            "threshold_bias": Decimal("0.0000"),
            "breakout_buffer": Decimal("0.0000"),
            "cooldown_seconds": 300,
        },
        "aggressive": {
            "target_notional": Decimal("1000"),
            "threshold_bias": Decimal("-0.0008"),
            "breakout_buffer": Decimal("0.0005"),
            "cooldown_seconds": 180,
        },
        "hft": {
            "target_notional": Decimal("250"),
            "threshold_bias": Decimal("-0.0012"),
            "breakout_buffer": Decimal("0.0008"),
            "cooldown_seconds": 90,
        },
    }
    default_profile: ProfileSettings = {
        "target_notional": Decimal("500"),
        "threshold_bias": Decimal("0.0000"),
        "breakout_buffer": Decimal("0.0000"),
        "cooldown_seconds": 300,
    }
    return profiles.get(profile_id, default_profile)


def _coerce_int(value: object) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value)
    return 0


def _coerce_datetime(value: object) -> datetime | None:
    return value if isinstance(value, datetime) else None


def _coerce_optional_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _recommended_quote_ttl_seconds(worker: MarketWorker) -> int:
    adapter_name = worker.adapter.metadata().adapter_name
    if adapter_name == "ig-forex-au":
        return 60
    if adapter_name == "alpaca":
        return 20
    if adapter_name == "binance":
        return 12
    return 8


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

