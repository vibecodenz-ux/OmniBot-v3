"""Evaluate runtime health, readiness, and degraded-state semantics."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from omnibot_v3.domain.broker import BrokerHealth, BrokerHealthStatus
from omnibot_v3.domain.health import (
    MarketHealthReport,
    RuntimeCadencePolicy,
    RuntimeHealthReport,
    RuntimeHealthState,
)
from omnibot_v3.domain.runtime import Market, MarketRuntimeSnapshot, RuntimeState
from omnibot_v3.domain.worker import MarketWorkerStatus


@dataclass(frozen=True, slots=True)
class RuntimeHealthEvaluator:
    cadence_policy: RuntimeCadencePolicy = RuntimeCadencePolicy()

    def evaluate(
        self,
        snapshots: dict[Market, MarketRuntimeSnapshot],
        worker_statuses: dict[Market, MarketWorkerStatus],
        worker_healths: dict[Market, BrokerHealth],
        now: datetime | None = None,
    ) -> RuntimeHealthReport:
        checked_at = now or datetime.now(UTC)
        reports = tuple(
            self._evaluate_market(
                market=market,
                snapshot=snapshots[market],
                worker_status=worker_statuses.get(market),
                worker_health=worker_healths.get(market),
                now=checked_at,
            )
            for market in (Market.STOCKS, Market.CRYPTO, Market.FOREX)
        )

        if any(report.state == RuntimeHealthState.UNHEALTHY for report in reports):
            overall_state = RuntimeHealthState.UNHEALTHY
        elif any(report.state == RuntimeHealthState.DEGRADED for report in reports):
            overall_state = RuntimeHealthState.DEGRADED
        else:
            overall_state = RuntimeHealthState.HEALTHY

        return RuntimeHealthReport(
            state=overall_state,
            ready=all(report.ready for report in reports),
            market_reports=reports,
            checked_at=checked_at,
        )

    def _evaluate_market(
        self,
        market: Market,
        snapshot: MarketRuntimeSnapshot,
        worker_status: MarketWorkerStatus | None,
        worker_health: BrokerHealth | None,
        now: datetime,
    ) -> MarketHealthReport:
        if snapshot.state == RuntimeState.ERROR:
            return MarketHealthReport(
                market=market,
                state=RuntimeHealthState.UNHEALTHY,
                ready=False,
                reason="market runtime is in error state",
            )

        if worker_health is not None and worker_health.status == BrokerHealthStatus.UNHEALTHY:
            return MarketHealthReport(
                market=market,
                state=RuntimeHealthState.UNHEALTHY,
                ready=False,
                reason="broker health check reported unhealthy",
            )

        degraded_reasons: list[str] = []
        if snapshot.state == RuntimeState.DISCONNECTED:
            if worker_health is not None and worker_health.status == BrokerHealthStatus.HEALTHY:
                degraded_reasons.append("market runtime is not started")
            else:
                degraded_reasons.append("market is disconnected")
        if snapshot.state in {RuntimeState.CONNECTING, RuntimeState.STOPPING}:
            degraded_reasons.append(f"market is {snapshot.state.lower()}")
        if snapshot.kill_switch_engaged:
            degraded_reasons.append("kill switch engaged")
        if snapshot.reconciliation_pending:
            degraded_reasons.append("reconciliation pending")
        if self._is_stale(snapshot.updated_at, now, self.cadence_policy.max_snapshot_age_seconds):
            degraded_reasons.append("runtime snapshot is stale")
        if worker_status is None:
            degraded_reasons.append("worker status unavailable")
        else:
            if worker_status.last_validated_at is None:
                degraded_reasons.append("worker has not been validated")
            elif self._is_stale(
                worker_status.last_validated_at,
                now,
                self.cadence_policy.max_worker_validation_age_seconds,
            ):
                degraded_reasons.append("worker validation is stale")
            if worker_status.last_health_check_at is None:
                degraded_reasons.append("worker health has not been checked")
            elif self._is_stale(
                worker_status.last_health_check_at,
                now,
                self.cadence_policy.max_worker_health_age_seconds,
            ):
                degraded_reasons.append("worker health check is stale")
            if worker_status.last_reconciled_at is None:
                degraded_reasons.append("worker portfolio has not been reconciled")
            elif self._is_stale(
                worker_status.last_reconciled_at,
                now,
                self.cadence_policy.max_worker_reconciliation_age_seconds,
            ):
                degraded_reasons.append("worker portfolio reconciliation is stale")
        if worker_health is not None and worker_health.status == BrokerHealthStatus.DEGRADED:
            degraded_reasons.append("broker health check reported degraded")
        if worker_health is not None and self._is_stale(
            worker_health.checked_at,
            now,
            self.cadence_policy.max_broker_health_age_seconds,
        ):
            degraded_reasons.append("broker health check is stale")

        if degraded_reasons:
            return MarketHealthReport(
                market=market,
                state=RuntimeHealthState.DEGRADED,
                ready=False,
                reason=", ".join(degraded_reasons),
            )

        return MarketHealthReport(
            market=market,
            state=RuntimeHealthState.HEALTHY,
            ready=True,
            reason="market ready",
        )

    def _is_stale(self, timestamp: datetime, now: datetime, max_age_seconds: int) -> bool:
        return now - timestamp > timedelta(seconds=max_age_seconds)