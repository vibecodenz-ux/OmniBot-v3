"""Supervision-friendly health and readiness probes for OmniBot runtime state."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from omnibot_v3.domain.health import RuntimeHealthReport, RuntimeHealthState
from omnibot_v3.domain.runtime import Market
from omnibot_v3.services.market_worker import MarketWorker
from omnibot_v3.services.orchestrator import TradingOrchestrator
from omnibot_v3.services.runtime_health import RuntimeHealthEvaluator

ProbeMode = Literal["health", "readiness"]


@dataclass(frozen=True, slots=True)
class RuntimeProbeService:
    evaluator: RuntimeHealthEvaluator = RuntimeHealthEvaluator()

    def probe(
        self,
        orchestrator: TradingOrchestrator,
        workers: dict[Market, MarketWorker],
        mode: ProbeMode,
        now: datetime | None = None,
    ) -> tuple[int, dict[str, object]]:
        report = self._collect_report(orchestrator=orchestrator, workers=workers, now=now)
        exit_code = self._exit_code(report, mode)
        payload = self._payload(report=report, mode=mode, exit_code=exit_code)
        return exit_code, payload

    def _collect_report(
        self,
        orchestrator: TradingOrchestrator,
        workers: dict[Market, MarketWorker],
        now: datetime | None,
    ) -> RuntimeHealthReport:
        worker_healths = {
            market: worker.health_check()
            for market, worker in workers.items()
        }
        worker_statuses = {
            market: worker.status()
            for market, worker in workers.items()
        }
        return self.evaluator.evaluate(
            snapshots=orchestrator.list_snapshots(),
            worker_statuses=worker_statuses,
            worker_healths=worker_healths,
            now=now,
        )

    def _exit_code(self, report: RuntimeHealthReport, mode: ProbeMode) -> int:
        if mode == "health":
            return 1 if report.state == RuntimeHealthState.UNHEALTHY else 0
        return 0 if report.ready else 1

    def _payload(
        self,
        report: RuntimeHealthReport,
        mode: ProbeMode,
        exit_code: int,
    ) -> dict[str, object]:
        return {
            "mode": mode,
            "exit_code": exit_code,
            "state": report.state.value,
            "ready": report.ready,
            "checked_at": report.checked_at.isoformat(),
            "market_reports": [
                {
                    "market": market_report.market.value,
                    "state": market_report.state.value,
                    "ready": market_report.ready,
                    "reason": market_report.reason,
                }
                for market_report in report.market_reports
            ],
        }
