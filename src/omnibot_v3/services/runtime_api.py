"""Application-layer adapter for future dashboard and HTTP runtime APIs."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from omnibot_v3.domain.api import (
    ApiCommandType,
    MarketControlResponse,
    MarketReconciliationResponse,
    MarketValidationResponse,
    PortfolioAnalyticsResponse,
    PortfolioOverviewResponse,
    RuntimeCommandRequest,
    RuntimeCommandResponse,
    RuntimeEventResponse,
    RuntimeHealthSummaryResponse,
    RuntimeOverviewResponse,
    UiStateResponse,
    build_market_control_response,
    build_market_reconciliation_response,
    build_market_validation_response,
    build_portfolio_analytics_response,
    build_portfolio_overview_response,
    build_runtime_command_response,
    build_runtime_health_summary_response,
    build_runtime_overview_response,
    build_ui_state_response,
    market_control_response_to_dict,
    market_reconciliation_response_to_dict,
    market_validation_response_to_dict,
    portfolio_analytics_response_to_dict,
    portfolio_overview_response_to_dict,
    runtime_command_request_to_domain,
    runtime_command_response_to_dict,
    runtime_health_summary_response_to_dict,
    runtime_overview_response_to_dict,
    ui_state_response_to_dict,
)
from omnibot_v3.domain.broker import BrokerHealth, BrokerHealthStatus
from omnibot_v3.domain.health import RuntimeHealthReport
from omnibot_v3.domain.runtime import InvalidStateTransitionError, Market, RuntimeState
from omnibot_v3.domain.worker import MarketWorkerStatus
from omnibot_v3.services.market_worker import MarketWorker
from omnibot_v3.services.orchestrator import TradingOrchestrator
from omnibot_v3.services.runtime_health import RuntimeHealthEvaluator
from omnibot_v3.services.runtime_store import PortfolioSnapshotStore


@dataclass(slots=True)
class RuntimeApiService:
    orchestrator: TradingOrchestrator
    workers: dict[Market, MarketWorker]
    portfolio_store: PortfolioSnapshotStore
    evaluator: RuntimeHealthEvaluator = RuntimeHealthEvaluator()
    auto_reconcile_portfolio_reads: bool = False
    portfolio_sync_ttl_seconds: int = 15
    health_check_ttl_seconds: int = 60
    on_market_started: Callable[[Market], None] | None = None
    on_market_stopped: Callable[[Market], None] | None = None
    _last_portfolio_sync_at: datetime | None = field(default=None, init=False)

    def get_runtime_overview(self, now: datetime | None = None) -> RuntimeOverviewResponse:
        report = self._collect_health_report(now=now)
        return build_runtime_overview_response(self.orchestrator.list_snapshots(), report)

    def get_runtime_overview_payload(self, now: datetime | None = None) -> dict[str, object]:
        return runtime_overview_response_to_dict(self.get_runtime_overview(now=now))

    def get_runtime_health(self, now: datetime | None = None) -> RuntimeHealthSummaryResponse:
        report = self._collect_health_report(now=now)
        return build_runtime_health_summary_response(report)

    def get_runtime_health_payload(self, now: datetime | None = None) -> dict[str, object]:
        return runtime_health_summary_response_to_dict(self.get_runtime_health(now=now))

    def get_market_health(self, market: Market) -> BrokerHealth:
        try:
            return self._worker_for(market).health_check(max_age_seconds=self.health_check_ttl_seconds)
        except Exception as exc:
            return BrokerHealth(status=BrokerHealthStatus.UNHEALTHY, message=str(exc))

    def get_portfolio_overview(
        self,
        now: datetime | None = None,
        *,
        sync_portfolios: bool | None = None,
    ) -> PortfolioOverviewResponse:
        if sync_portfolios if sync_portfolios is not None else self.auto_reconcile_portfolio_reads:
            self.synchronize_portfolios()
        generated_at = now or datetime.now(UTC)
        return build_portfolio_overview_response(
            self.portfolio_store.load_all(),
            generated_at=generated_at,
        )

    def get_portfolio_overview_payload(
        self,
        now: datetime | None = None,
        *,
        sync_portfolios: bool | None = None,
    ) -> dict[str, object]:
        return portfolio_overview_response_to_dict(
            self.get_portfolio_overview(now=now, sync_portfolios=sync_portfolios)
        )

    def get_portfolio_analytics(
        self,
        now: datetime | None = None,
        *,
        sync_portfolios: bool | None = None,
    ) -> PortfolioAnalyticsResponse:
        if sync_portfolios if sync_portfolios is not None else self.auto_reconcile_portfolio_reads:
            self.synchronize_portfolios()
        generated_at = now or datetime.now(UTC)
        return build_portfolio_analytics_response(
            self.portfolio_store.load_all(),
            generated_at=generated_at,
        )

    def get_portfolio_analytics_payload(
        self,
        now: datetime | None = None,
        *,
        sync_portfolios: bool | None = None,
    ) -> dict[str, object]:
        return portfolio_analytics_response_to_dict(
            self.get_portfolio_analytics(now=now, sync_portfolios=sync_portfolios)
        )

    def get_ui_state(self, now: datetime | None = None) -> UiStateResponse:
        return build_ui_state_response(
            runtime=self.get_runtime_overview(now=now),
            health=self.get_runtime_health(now=now),
            portfolio=self.get_portfolio_overview(now=now),
            analytics=self.get_portfolio_analytics(now=now),
        )

    def get_ui_state_payload(self, now: datetime | None = None) -> dict[str, object]:
        return ui_state_response_to_dict(self.get_ui_state(now=now))

    def execute_command(self, request: RuntimeCommandRequest) -> RuntimeCommandResponse:
        command = runtime_command_request_to_domain(request)
        events = self.orchestrator.handle(command)
        return build_runtime_command_response(request, events)

    def execute_command_payload(self, request: RuntimeCommandRequest) -> dict[str, object]:
        return runtime_command_response_to_dict(self.execute_command(request))

    def validate_market(self, market: Market) -> MarketValidationResponse:
        return build_market_validation_response(self._worker_for(market).validate_configuration())

    def validate_market_payload(self, market: Market) -> dict[str, object]:
        return market_validation_response_to_dict(self.validate_market(market))

    def arm_market(self, market: Market) -> MarketControlResponse:
        if self.orchestrator.snapshot(market).state == RuntimeState.ARMED:
            return build_market_control_response(
                market,
                RuntimeCommandResponse(
                    command=ApiCommandType.ARM_MARKET.value,
                    event_count=0,
                    events=(),
                ),
            )
        return self._execute_market_control(ApiCommandType.ARM_MARKET, market)

    def arm_market_payload(self, market: Market) -> dict[str, object]:
        return market_control_response_to_dict(self.arm_market(market))

    def disarm_market(self, market: Market) -> MarketControlResponse:
        if self.orchestrator.snapshot(market).state == RuntimeState.IDLE:
            return build_market_control_response(
                market,
                RuntimeCommandResponse(
                    command=ApiCommandType.DISARM_MARKET.value,
                    event_count=0,
                    events=(),
                ),
            )
        return self._execute_market_control(ApiCommandType.DISARM_MARKET, market)

    def disarm_market_payload(self, market: Market) -> dict[str, object]:
        return market_control_response_to_dict(self.disarm_market(market))

    def start_market(self, market: Market) -> MarketControlResponse:
        snapshot = self.orchestrator.snapshot(market)
        if snapshot.state in {RuntimeState.CONNECTING, RuntimeState.STOPPING, RuntimeState.ERROR}:
            raise InvalidStateTransitionError(
                f"Cannot start market {market.value} while in state {snapshot.state.value}."
            )

        requests: list[RuntimeCommandRequest] = []
        current_state = snapshot.state
        if current_state == RuntimeState.DISCONNECTED:
            requests.append(RuntimeCommandRequest(command=ApiCommandType.CONNECT_MARKET, market=market))
            current_state = RuntimeState.IDLE
        if current_state == RuntimeState.IDLE:
            requests.append(RuntimeCommandRequest(command=ApiCommandType.ARM_MARKET, market=market))
            current_state = RuntimeState.ARMED
        if current_state == RuntimeState.ARMED:
            requests.append(RuntimeCommandRequest(command=ApiCommandType.START_MARKET, market=market))

        response = build_market_control_response(
            market,
            self._execute_market_sequence(ApiCommandType.START_MARKET, tuple(requests)),
        )
        if self.on_market_started is not None:
            self.on_market_started(market)
        return response

    def start_market_payload(self, market: Market) -> dict[str, object]:
        return market_control_response_to_dict(self.start_market(market))

    def stop_market(self, market: Market) -> MarketControlResponse:
        snapshot = self.orchestrator.snapshot(market)
        if snapshot.state in {RuntimeState.CONNECTING, RuntimeState.STOPPING, RuntimeState.ERROR}:
            raise InvalidStateTransitionError(
                f"Cannot stop market {market.value} while in state {snapshot.state.value}."
            )

        requests: list[RuntimeCommandRequest] = []
        current_state = snapshot.state
        if current_state == RuntimeState.RUNNING:
            requests.append(RuntimeCommandRequest(command=ApiCommandType.STOP_MARKET, market=market))
            current_state = RuntimeState.ARMED
        if current_state == RuntimeState.ARMED:
            requests.append(RuntimeCommandRequest(command=ApiCommandType.DISARM_MARKET, market=market))

        response = build_market_control_response(
            market,
            self._execute_market_sequence(ApiCommandType.STOP_MARKET, tuple(requests)),
        )
        if self.on_market_stopped is not None:
            self.on_market_stopped(market)
        return response

    def stop_market_payload(self, market: Market) -> dict[str, object]:
        return market_control_response_to_dict(self.stop_market(market))

    def reconcile_market(self, market: Market) -> MarketReconciliationResponse:
        request_response = self.execute_command(
            RuntimeCommandRequest(command=ApiCommandType.RECONCILE_MARKET, market=market)
        )
        snapshot = self._worker_for(market).reconcile_portfolio()
        self.portfolio_store.save(snapshot)
        completion_response = self.execute_command(
            RuntimeCommandRequest(
                command=ApiCommandType.COMPLETE_MARKET_RECONCILIATION,
                market=market,
            )
        )
        combined_response = RuntimeCommandResponse(
            command=ApiCommandType.RECONCILE_MARKET.value,
            event_count=request_response.event_count + completion_response.event_count,
            events=request_response.events + completion_response.events,
        )
        reconciled_at = self._worker_for(market).status().last_reconciled_at
        if reconciled_at is None:
            raise ValueError(f"Market {market.value} did not record a reconciliation timestamp.")
        return build_market_reconciliation_response(market, combined_response, reconciled_at)

    def reconcile_market_payload(self, market: Market) -> dict[str, object]:
        return market_reconciliation_response_to_dict(self.reconcile_market(market))

    def synchronize_portfolios(self, *, force: bool = False, now: datetime | None = None) -> None:
        observed_at = now or datetime.now(UTC)
        if (
            not force
            and self._last_portfolio_sync_at is not None
            and observed_at - self._last_portfolio_sync_at <= timedelta(seconds=self.portfolio_sync_ttl_seconds)
        ):
            return

        eligible_workers = {
            market: worker
            for market, worker in self.workers.items()
            if worker.validate_configuration().valid
        }
        with ThreadPoolExecutor(max_workers=max(1, len(eligible_workers))) as executor:
            future_by_market = {
                executor.submit(worker.reconcile_portfolio): market
                for market, worker in eligible_workers.items()
            }
            for future in as_completed(future_by_market):
                market = future_by_market[future]
                try:
                    snapshot = future.result()
                except Exception:
                    continue
                self.portfolio_store.save(snapshot)
                self.orchestrator.heartbeat(market, last_reconciled_at=self.workers[market].status().last_reconciled_at)
        self._last_portfolio_sync_at = observed_at

    def _collect_health_report(self, now: datetime | None = None) -> RuntimeHealthReport:
        with ThreadPoolExecutor(max_workers=max(1, len(self.workers))) as executor:
            future_by_market = {
                executor.submit(self.get_market_health, market): market
                for market in self.workers
            }
            worker_healths: dict[Market, BrokerHealth] = {}
            for future in as_completed(future_by_market):
                market = future_by_market[future]
                worker_healths[market] = future.result()
        worker_statuses: dict[Market, MarketWorkerStatus] = {
            market: worker.status() for market, worker in self.workers.items()
        }
        return self.evaluator.evaluate(
            snapshots=self.orchestrator.list_snapshots(),
            worker_statuses=worker_statuses,
            worker_healths=worker_healths,
            now=now,
        )

    def _execute_market_control(
        self,
        command: ApiCommandType,
        market: Market,
    ) -> MarketControlResponse:
        response = self.execute_command(RuntimeCommandRequest(command=command, market=market))
        return build_market_control_response(market, response)

    def _execute_market_sequence(
        self,
        command: ApiCommandType,
        requests: tuple[RuntimeCommandRequest, ...],
    ) -> RuntimeCommandResponse:
        combined_events: list[RuntimeEventResponse] = []
        for request in requests:
            response = self.execute_command(request)
            combined_events.extend(response.events)

        return RuntimeCommandResponse(
            command=command.value,
            event_count=len(combined_events),
            events=tuple(combined_events),
        )

    def _worker_for(self, market: Market) -> MarketWorker:
        try:
            return self.workers[market]
        except KeyError as exc:
            raise ValueError(f"No worker configured for market {market.value}.") from exc