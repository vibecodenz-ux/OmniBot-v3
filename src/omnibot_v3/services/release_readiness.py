"""Release-readiness validation helpers for OmniBot v3."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from omnibot_v3.domain import ArmMarket, ConnectMarket, Market, StartMarket
from omnibot_v3.domain.api import ApiCommandType, RuntimeCommandRequest
from omnibot_v3.domain.runtime import InvalidStateTransitionError, RuntimeState
from omnibot_v3.infra.backup_restore import (
    PostgresBackupConfig,
    build_backup_plan,
    build_restore_validation_report,
)
from omnibot_v3.infra.runtime_store import (
    InMemoryPortfolioSnapshotStore,
    InMemoryRuntimeEventStore,
    InMemoryRuntimeSnapshotStore,
)
from omnibot_v3.services.market_integrations import build_default_market_workers
from omnibot_v3.services.orchestrator import TradingOrchestrator, build_default_orchestrator
from omnibot_v3.services.runtime_api import RuntimeApiService


@dataclass(frozen=True, slots=True)
class ReleaseReadinessCheck:
    name: str
    passed: bool
    detail: str


@dataclass(frozen=True, slots=True)
class ReleaseReadinessReport:
    checked_at: str
    passed: bool
    check_count: int
    checks: tuple[ReleaseReadinessCheck, ...]


@dataclass(frozen=True, slots=True)
class ReleaseReadinessService:
    def run(self, *, checked_at: datetime | None = None) -> ReleaseReadinessReport:
        checks = [
            self._broker_validation_check(),
            self._arming_rules_check(),
            self._portfolio_snapshot_stability_check(),
            self._startup_recovery_check(),
            self._backup_restore_paths_check(),
        ]
        timestamp = checked_at or datetime.now(UTC)
        return ReleaseReadinessReport(
            checked_at=timestamp.isoformat(),
            passed=all(check.passed for check in checks),
            check_count=len(checks),
            checks=tuple(checks),
        )

    def report_to_dict(self, report: ReleaseReadinessReport) -> dict[str, object]:
        return {
            "checked_at": report.checked_at,
            "passed": report.passed,
            "check_count": report.check_count,
            "checks": [asdict(check) for check in report.checks],
        }

    def _broker_validation_check(self) -> ReleaseReadinessCheck:
        workers = build_default_market_workers()
        validated_markets: list[str] = []

        for market, worker in workers.items():
            result = worker.validate_configuration()
            metadata = worker.adapter.metadata()
            if not result.valid:
                return ReleaseReadinessCheck(
                    name="broker-validation",
                    passed=False,
                    detail=f"Worker validation failed for {market.value}: {', '.join(result.errors)}",
                )
            if not metadata.safety_policy.require_market_arming:
                return ReleaseReadinessCheck(
                    name="broker-validation",
                    passed=False,
                    detail=f"Broker metadata for {market.value} does not require explicit market arming.",
                )
            validated_markets.append(market.value)

        return ReleaseReadinessCheck(
            name="broker-validation",
            passed=True,
            detail=f"Validated worker and broker safety metadata for {', '.join(validated_markets)}.",
        )

    def _arming_rules_check(self) -> ReleaseReadinessCheck:
        orchestrator = build_default_orchestrator()
        workers = build_default_market_workers()
        service = RuntimeApiService(
            orchestrator=orchestrator,
            workers=workers,
            portfolio_store=InMemoryPortfolioSnapshotStore(),
        )

        try:
            service.arm_market(Market.STOCKS)
        except InvalidStateTransitionError:
            pass
        else:
            return ReleaseReadinessCheck(
                name="arming-rules",
                passed=False,
                detail="Stocks market armed while disconnected.",
            )

        service.execute_command(RuntimeCommandRequest(command=ApiCommandType.CONNECT_MARKET, market=Market.STOCKS))
        service.arm_market(Market.STOCKS)
        service.start_market(Market.STOCKS)
        service.stop_market(Market.STOCKS)
        service.disarm_market(Market.STOCKS)

        final_state = orchestrator.snapshot(Market.STOCKS).state
        if final_state is not RuntimeState.IDLE:
            return ReleaseReadinessCheck(
                name="arming-rules",
                passed=False,
                detail=f"Expected stocks market to return to IDLE after stop and disarm, got {final_state}.",
            )

        return ReleaseReadinessCheck(
            name="arming-rules",
            passed=True,
            detail="Disconnected markets cannot arm, and the connect-arm-start-stop-disarm flow returns to IDLE.",
        )

    def _portfolio_snapshot_stability_check(self) -> ReleaseReadinessCheck:
        orchestrator = build_default_orchestrator()
        workers = build_default_market_workers()
        portfolio_store = InMemoryPortfolioSnapshotStore()
        service = RuntimeApiService(
            orchestrator=orchestrator,
            workers=workers,
            portfolio_store=portfolio_store,
        )

        service.execute_command(RuntimeCommandRequest(command=ApiCommandType.CONNECT_MARKET, market=Market.STOCKS))
        service.reconcile_market(Market.STOCKS)

        overview = service.get_portfolio_overview_payload()
        analytics = service.get_portfolio_analytics_payload()
        stats = overview["markets"], analytics["stats"], analytics["charts"]
        markets_payload, stats_payload, charts_payload = stats
        if not isinstance(markets_payload, list) or not isinstance(stats_payload, list) or not isinstance(charts_payload, list):
            return ReleaseReadinessCheck(
                name="portfolio-stability",
                passed=False,
                detail="Portfolio or analytics payloads did not serialize as expected lists.",
            )

        overview_snapshot_count = overview.get("snapshot_count")
        analytics_snapshot_count = analytics.get("snapshot_count")
        if not isinstance(overview_snapshot_count, int) or not isinstance(analytics_snapshot_count, int):
            return ReleaseReadinessCheck(
                name="portfolio-stability",
                passed=False,
                detail="Portfolio or analytics payloads did not expose integer snapshot counts.",
            )

        if overview_snapshot_count < 1 or analytics_snapshot_count < 1:
            return ReleaseReadinessCheck(
                name="portfolio-stability",
                passed=False,
                detail="Reconciliation did not produce stored portfolio or analytics snapshots.",
            )

        total_value_metric = next(
            (stat for stat in stats_payload if stat.get("metric_id") == "total-portfolio-value"),
            None,
        )
        if not isinstance(total_value_metric, dict):
            return ReleaseReadinessCheck(
                name="portfolio-stability",
                passed=False,
                detail="Portfolio analytics did not expose the total portfolio value metric.",
            )

        provenance = total_value_metric.get("provenance")
        if not isinstance(provenance, dict) or provenance.get("source_type") != "stored-latest-portfolio-snapshot":
            return ReleaseReadinessCheck(
                name="portfolio-stability",
                passed=False,
                detail="Portfolio analytics provenance was not sourced from stored latest snapshots.",
            )

        if overview["total_portfolio_value"] != total_value_metric.get("value"):
            return ReleaseReadinessCheck(
                name="portfolio-stability",
                passed=False,
                detail="Portfolio overview total did not match analytics total-portfolio-value metric.",
            )

        return ReleaseReadinessCheck(
            name="portfolio-stability",
            passed=True,
            detail="Stored reconciliation snapshots drive both portfolio totals and analytics with matching values and provenance.",
        )

    def _startup_recovery_check(self) -> ReleaseReadinessCheck:
        snapshot_store = InMemoryRuntimeSnapshotStore()
        event_store = InMemoryRuntimeEventStore()

        seed_orchestrator = TradingOrchestrator(
            snapshot_store=snapshot_store,
            event_store=event_store,
            auto_recover_on_startup=False,
        )
        seed_orchestrator.handle(ConnectMarket(market=Market.STOCKS))
        seed_orchestrator.handle(ArmMarket(market=Market.STOCKS))
        seed_orchestrator.handle(StartMarket(market=Market.STOCKS))

        recovered = TradingOrchestrator(
            snapshot_store=snapshot_store,
            event_store=event_store,
            auto_recover_on_startup=True,
        )

        if recovered.snapshot(Market.STOCKS).state is not RuntimeState.ARMED:
            return ReleaseReadinessCheck(
                name="startup-recovery",
                passed=False,
                detail="Automatic startup recovery did not move a persisted RUNNING market back to ARMED.",
            )

        recovery_events = [
            event
            for event in recovered.audit_log
            if getattr(event, "reason", None) == "startup recovery"
        ]
        if len(recovery_events) < 2:
            return ReleaseReadinessCheck(
                name="startup-recovery",
                passed=False,
                detail="Automatic startup recovery did not emit the expected audit events.",
            )

        return ReleaseReadinessCheck(
            name="startup-recovery",
            passed=True,
            detail="Persisted RUNNING markets are automatically recovered to ARMED on orchestrator startup with audit events recorded.",
        )

    def _backup_restore_paths_check(self) -> ReleaseReadinessCheck:
        config = PostgresBackupConfig(
            database_url="postgresql://omnibot:secret@localhost:5432/omnibot",
            output_directory=Path("/var/backups/omnibot"),
        )
        backup_plan = build_backup_plan(
            config,
            timestamp=datetime(2026, 3, 31, 12, 0, tzinfo=UTC),
        )
        restore_report = build_restore_validation_report(config, backup_plan.backup_file)

        if "--schema=omnibot" not in backup_plan.command or "--schema=omnibot_archive" not in backup_plan.command:
            return ReleaseReadinessCheck(
                name="backup-restore-paths",
                passed=False,
                detail="Backup plan does not include both active and archive schemas.",
            )
        if restore_report.command[0] != "pg_restore" or len(restore_report.validation_queries) < 3:
            return ReleaseReadinessCheck(
                name="backup-restore-paths",
                passed=False,
                detail="Restore validation report is missing the restore command or validation queries.",
            )

        return ReleaseReadinessCheck(
            name="backup-restore-paths",
            passed=True,
            detail="Backup planning includes active and archive schemas, and restore validation emits the restore command plus verification queries.",
        )