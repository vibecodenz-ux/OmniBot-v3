"""In-memory orchestrator for explicit market runtime transitions."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from omnibot_v3.domain.contracts import (
    ArmMarket,
    CompleteMarketReconciliation,
    ConnectMarket,
    DisarmMarket,
    DisconnectMarket,
    EmergencyDisarmAll,
    EngageKillSwitch,
    GracefulShutdownAll,
    MarketKillSwitchEngaged,
    MarketKillSwitchReleased,
    MarketReconciliationCompleted,
    MarketReconciliationRequested,
    MarketStateTransitioned,
    MarkMarketError,
    ReconcileMarket,
    RecoverRuntime,
    ReleaseKillSwitch,
    RuntimeCommand,
    RuntimeEvent,
    StartMarket,
    StopMarket,
)
from omnibot_v3.domain.runtime import (
    InvalidStateTransitionError,
    Market,
    MarketRuntimeSnapshot,
    RuntimeState,
)
from omnibot_v3.infra.runtime_store import InMemoryRuntimeEventStore, InMemoryRuntimeSnapshotStore
from omnibot_v3.services.runtime_store import RuntimeEventStore, RuntimeSnapshotStore


@dataclass(slots=True)
class TradingOrchestrator:
    """Coordinates market-level runtime commands behind explicit service contracts."""

    markets: dict[Market, MarketRuntimeSnapshot] = field(default_factory=dict)
    audit_log: list[RuntimeEvent] = field(default_factory=list)
    snapshot_store: RuntimeSnapshotStore | None = None
    event_store: RuntimeEventStore | None = None
    auto_recover_on_startup: bool = True

    def __post_init__(self) -> None:
        stored_markets = self.snapshot_store.load_all() if self.snapshot_store is not None else {}
        if not self.markets:
            self.markets = {}

        for market in (Market.STOCKS, Market.CRYPTO, Market.FOREX):
            self.markets.setdefault(
                market,
                stored_markets.get(market, MarketRuntimeSnapshot(market=market)),
            )

        if self.snapshot_store is not None:
            for snapshot in self.markets.values():
                self.snapshot_store.save(snapshot)

        if self.event_store is not None and not self.audit_log:
            self.audit_log = self.event_store.list_events()

        if self.auto_recover_on_startup:
            recovery_events = self._recover_runtime()
            if recovery_events:
                self.audit_log.extend(recovery_events)
                if self.event_store is not None:
                    self.event_store.append(recovery_events)
                if self.snapshot_store is not None:
                    self._persist_markets_for(recovery_events)

    def snapshot(self, market: Market) -> MarketRuntimeSnapshot:
        return self.markets[market]

    def handle(
        self,
        command: RuntimeCommand | EmergencyDisarmAll | RecoverRuntime | GracefulShutdownAll,
    ) -> list[RuntimeEvent]:
        if isinstance(command, ConnectMarket):
            events = self._connect_market(command.market)
        elif isinstance(command, DisconnectMarket):
            events = self._disconnect_market(command.market)
        elif isinstance(command, ArmMarket):
            self._ensure_market_not_killed(command.market)
            events = [self._transition(command.market, RuntimeState.ARMED, "market armed")]
        elif isinstance(command, DisarmMarket):
            events = [self._transition(command.market, RuntimeState.IDLE, "market disarmed")]
        elif isinstance(command, StartMarket):
            self._ensure_market_not_killed(command.market)
            events = [self._transition(command.market, RuntimeState.RUNNING, "market started")]
        elif isinstance(command, StopMarket):
            events = self._stop_market(command.market)
        elif isinstance(command, ReconcileMarket):
            events = self._request_reconciliation(command.market)
        elif isinstance(command, CompleteMarketReconciliation):
            events = self._complete_reconciliation(command.market)
        elif isinstance(command, EngageKillSwitch):
            events = self._engage_kill_switch(command.market, command.reason)
        elif isinstance(command, ReleaseKillSwitch):
            events = [self._release_kill_switch(command.market, command.reason)]
        elif isinstance(command, MarkMarketError):
            events = [self._mark_market_error(command.market, command.message)]
        elif isinstance(command, EmergencyDisarmAll):
            events = self._emergency_disarm_all()
        elif isinstance(command, RecoverRuntime):
            events = self._recover_runtime()
        elif isinstance(command, GracefulShutdownAll):
            events = self._graceful_shutdown_all()
        else:
            raise TypeError(f"Unsupported command type: {type(command).__name__}")

        self.audit_log.extend(events)
        if self.event_store is not None:
            self.event_store.append(events)
        if self.snapshot_store is not None:
            self._persist_markets_for(events)
        return events

    def list_snapshots(self) -> dict[Market, MarketRuntimeSnapshot]:
        return dict(self.markets)

    def heartbeat(
        self,
        market: Market,
        *,
        occurred_at: datetime | None = None,
        last_reconciled_at: datetime | None = None,
    ) -> None:
        snapshot = self.snapshot(market)
        timestamp = occurred_at or datetime.now(tz=UTC)
        self.markets[market] = MarketRuntimeSnapshot(
            market=market,
            state=snapshot.state,
            kill_switch_engaged=snapshot.kill_switch_engaged,
            reconciliation_pending=snapshot.reconciliation_pending,
            last_reconciled_at=last_reconciled_at or snapshot.last_reconciled_at,
            last_error=snapshot.last_error,
            updated_at=timestamp,
        )
        if self.snapshot_store is not None:
            self.snapshot_store.save(self.markets[market])

    def _connect_market(self, market: Market) -> list[RuntimeEvent]:
        current_state = self.snapshot(market).state
        if current_state not in {RuntimeState.DISCONNECTED, RuntimeState.ERROR}:
            raise InvalidStateTransitionError(
                f"Cannot connect market {market} while in state {current_state}."
            )

        return [
            self._transition(market, RuntimeState.CONNECTING, "connection initiated"),
            self._transition(market, RuntimeState.IDLE, "connection established"),
        ]

    def _disconnect_market(self, market: Market) -> list[RuntimeEvent]:
        current_state = self.snapshot(market).state
        if current_state == RuntimeState.RUNNING:
            raise InvalidStateTransitionError(
                f"Cannot disconnect market {market} while it is running; stop it first."
            )
        if current_state == RuntimeState.DISCONNECTED:
            raise InvalidStateTransitionError(f"Market {market} is already disconnected.")

        return [self._transition(market, RuntimeState.DISCONNECTED, "market disconnected")]

    def _stop_market(self, market: Market) -> list[RuntimeEvent]:
        return [
            self._transition(market, RuntimeState.STOPPING, "market stopping"),
            self._transition(market, RuntimeState.ARMED, "market stopped"),
        ]

    def _request_reconciliation(self, market: Market) -> list[RuntimeEvent]:
        snapshot = self.snapshot(market)
        current_state = snapshot.state
        if current_state in {RuntimeState.DISCONNECTED, RuntimeState.CONNECTING}:
            raise InvalidStateTransitionError(
                f"Cannot reconcile market {market} while in state {current_state}."
            )
        if snapshot.reconciliation_pending:
            return []

        event = MarketReconciliationRequested(market=market)
        self.markets[market] = MarketRuntimeSnapshot(
            market=market,
            state=snapshot.state,
            kill_switch_engaged=snapshot.kill_switch_engaged,
            reconciliation_pending=True,
            last_reconciled_at=snapshot.last_reconciled_at,
            last_error=snapshot.last_error,
            updated_at=event.occurred_at,
        )
        return [event]

    def _complete_reconciliation(self, market: Market) -> list[RuntimeEvent]:
        snapshot = self.snapshot(market)
        if not snapshot.reconciliation_pending:
            return []

        event = MarketReconciliationCompleted(market=market)
        self.markets[market] = MarketRuntimeSnapshot(
            market=market,
            state=snapshot.state,
            kill_switch_engaged=snapshot.kill_switch_engaged,
            reconciliation_pending=False,
            last_reconciled_at=event.occurred_at,
            last_error=snapshot.last_error,
            updated_at=event.occurred_at,
        )
        return [event]

    def _engage_kill_switch(self, market: Market, reason: str) -> list[RuntimeEvent]:
        snapshot = self.snapshot(market)
        if snapshot.kill_switch_engaged:
            raise InvalidStateTransitionError(f"Market {market} kill switch is already engaged.")

        events: list[RuntimeEvent] = []
        if snapshot.state == RuntimeState.RUNNING:
            events.append(
                self._transition(market, RuntimeState.STOPPING, "kill switch stopping market")
            )
            events.append(
                self._transition(market, RuntimeState.IDLE, "kill switch disarmed market")
            )
        elif snapshot.state == RuntimeState.ARMED:
            events.append(
                self._transition(market, RuntimeState.IDLE, "kill switch disarmed market")
            )

        latest_snapshot = self.snapshot(market)
        kill_event = MarketKillSwitchEngaged(
            market=market,
            previous_state=latest_snapshot.state,
            new_state=latest_snapshot.state,
            reason=reason,
        )
        self.markets[market] = MarketRuntimeSnapshot(
            market=market,
            state=latest_snapshot.state,
            kill_switch_engaged=True,
            reconciliation_pending=latest_snapshot.reconciliation_pending,
            last_reconciled_at=latest_snapshot.last_reconciled_at,
            last_error=latest_snapshot.last_error,
            updated_at=kill_event.occurred_at,
        )
        events.append(kill_event)
        return events

    def _release_kill_switch(self, market: Market, reason: str) -> RuntimeEvent:
        snapshot = self.snapshot(market)
        if not snapshot.kill_switch_engaged:
            raise InvalidStateTransitionError(f"Market {market} kill switch is not engaged.")

        event = MarketKillSwitchReleased(market=market, reason=reason)
        self.markets[market] = MarketRuntimeSnapshot(
            market=market,
            state=snapshot.state,
            kill_switch_engaged=False,
            reconciliation_pending=snapshot.reconciliation_pending,
            last_reconciled_at=snapshot.last_reconciled_at,
            last_error=snapshot.last_error,
            updated_at=event.occurred_at,
        )
        return event

    def _emergency_disarm_all(self) -> list[RuntimeEvent]:
        events: list[RuntimeEvent] = []
        for market in (Market.STOCKS, Market.CRYPTO, Market.FOREX):
            state = self.snapshot(market).state
            if state == RuntimeState.RUNNING:
                events.append(
                    self._transition(market, RuntimeState.STOPPING, "global emergency disarm")
                )
                events.append(
                    self._transition(market, RuntimeState.IDLE, "global emergency disarm")
                )
            elif state == RuntimeState.ARMED:
                events.append(
                    self._transition(market, RuntimeState.IDLE, "global emergency disarm")
                )
        return events

    def _recover_runtime(self) -> list[RuntimeEvent]:
        events: list[RuntimeEvent] = []
        for market in (Market.STOCKS, Market.CRYPTO, Market.FOREX):
            snapshot = self.snapshot(market)
            if snapshot.state == RuntimeState.RUNNING:
                events.append(self._transition(market, RuntimeState.STOPPING, "startup recovery"))
                events.append(self._transition(market, RuntimeState.ARMED, "startup recovery"))
            elif snapshot.state == RuntimeState.STOPPING:
                events.append(self._transition(market, RuntimeState.ARMED, "startup recovery"))
            elif snapshot.state == RuntimeState.CONNECTING:
                events.append(self._transition(market, RuntimeState.IDLE, "startup recovery"))
        return events

    def _graceful_shutdown_all(self) -> list[RuntimeEvent]:
        events: list[RuntimeEvent] = []
        for market in (Market.STOCKS, Market.CRYPTO, Market.FOREX):
            snapshot = self.snapshot(market)
            if snapshot.state == RuntimeState.RUNNING:
                events.append(self._transition(market, RuntimeState.STOPPING, "graceful shutdown"))
                events.append(self._transition(market, RuntimeState.IDLE, "graceful shutdown"))
                events.append(
                    self._transition(market, RuntimeState.DISCONNECTED, "graceful shutdown")
                )
            elif snapshot.state == RuntimeState.ARMED:
                events.append(self._transition(market, RuntimeState.IDLE, "graceful shutdown"))
                events.append(
                    self._transition(market, RuntimeState.DISCONNECTED, "graceful shutdown")
                )
            elif snapshot.state == RuntimeState.IDLE:
                events.append(
                    self._transition(market, RuntimeState.DISCONNECTED, "graceful shutdown")
                )
            elif snapshot.state == RuntimeState.ERROR:
                events.append(self._disconnect_from_error(market))
        return events

    def _disconnect_from_error(self, market: Market) -> MarketStateTransitioned:
        snapshot = self.snapshot(market)
        event = MarketStateTransitioned(
            market=market,
            previous_state=snapshot.state,
            new_state=RuntimeState.DISCONNECTED,
            reason="graceful shutdown",
        )
        self.markets[market] = MarketRuntimeSnapshot(
            market=market,
            state=RuntimeState.DISCONNECTED,
            kill_switch_engaged=snapshot.kill_switch_engaged,
            reconciliation_pending=snapshot.reconciliation_pending,
            last_reconciled_at=snapshot.last_reconciled_at,
            last_error=snapshot.last_error,
            updated_at=event.occurred_at,
        )
        return event

    def _mark_market_error(self, market: Market, message: str) -> RuntimeEvent:
        snapshot = self.snapshot(market)
        event = MarketStateTransitioned(
            market=market,
            previous_state=snapshot.state,
            new_state=RuntimeState.ERROR,
            reason=message,
        )
        self.markets[market] = MarketRuntimeSnapshot(
            market=market,
            state=RuntimeState.ERROR,
            kill_switch_engaged=snapshot.kill_switch_engaged,
            reconciliation_pending=snapshot.reconciliation_pending,
            last_reconciled_at=snapshot.last_reconciled_at,
            last_error=message,
            updated_at=event.occurred_at,
        )
        return event

    def _transition(
        self,
        market: Market,
        new_state: RuntimeState,
        reason: str,
    ) -> MarketStateTransitioned:
        snapshot = self.snapshot(market)
        self._validate_transition(snapshot.state, new_state, market)
        event = MarketStateTransitioned(
            market=market,
            previous_state=snapshot.state,
            new_state=new_state,
            reason=reason,
        )
        self.markets[market] = MarketRuntimeSnapshot(
            market=market,
            state=new_state,
            kill_switch_engaged=snapshot.kill_switch_engaged,
            reconciliation_pending=snapshot.reconciliation_pending,
            last_reconciled_at=snapshot.last_reconciled_at,
            last_error=None,
            updated_at=event.occurred_at,
        )
        return event

    def _ensure_market_not_killed(self, market: Market) -> None:
        if self.snapshot(market).kill_switch_engaged:
            raise InvalidStateTransitionError(
                f"Cannot trade market {market} while its kill switch is engaged."
            )

    def _validate_transition(
        self,
        current_state: RuntimeState,
        new_state: RuntimeState,
        market: Market,
    ) -> None:
        allowed_transitions = {
            RuntimeState.DISCONNECTED: {RuntimeState.CONNECTING},
            RuntimeState.CONNECTING: {RuntimeState.IDLE, RuntimeState.ERROR},
            RuntimeState.IDLE: {RuntimeState.ARMED, RuntimeState.DISCONNECTED, RuntimeState.ERROR},
            RuntimeState.ARMED: {
                RuntimeState.RUNNING,
                RuntimeState.IDLE,
                RuntimeState.DISCONNECTED,
                RuntimeState.ERROR,
            },
            RuntimeState.RUNNING: {RuntimeState.STOPPING, RuntimeState.ERROR},
            RuntimeState.STOPPING: {RuntimeState.ARMED, RuntimeState.IDLE, RuntimeState.ERROR},
            RuntimeState.ERROR: {RuntimeState.CONNECTING, RuntimeState.DISCONNECTED},
        }
        if new_state not in allowed_transitions[current_state]:
            raise InvalidStateTransitionError(
                f"Invalid transition for market {market}: {current_state} -> {new_state}."
            )

    def _persist_markets_for(self, events: list[RuntimeEvent]) -> None:
        if self.snapshot_store is None:
            return

        changed_markets = {event.market for event in events}
        for market in changed_markets:
            self.snapshot_store.save(self.markets[market])


def build_default_orchestrator() -> TradingOrchestrator:
    return TradingOrchestrator(
        snapshot_store=InMemoryRuntimeSnapshotStore(),
        event_store=InMemoryRuntimeEventStore(),
    )
