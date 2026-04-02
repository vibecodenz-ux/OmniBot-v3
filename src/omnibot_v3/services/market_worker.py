"""Market worker wrapper around broker adapters with validation and status tracking."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from omnibot_v3.domain.broker import (
    BrokerCapability,
    BrokerEnvironment,
    BrokerHealth,
    NormalizedOrder,
    NormalizedTrade,
    OrderRequest,
    PortfolioSnapshot,
)
from omnibot_v3.domain.worker import (
    MarketWorkerSettings,
    MarketWorkerStatus,
    MarketWorkerValidationResult,
)
from omnibot_v3.services.broker_adapter import BrokerAdapter


@dataclass(slots=True)
class MarketWorker:
    settings: MarketWorkerSettings
    adapter: BrokerAdapter
    _last_validated_at: datetime | None = field(default=None, init=False)
    _last_health_check_at: datetime | None = field(default=None, init=False)
    _last_reconciled_at: datetime | None = field(default=None, init=False)
    _last_health: BrokerHealth | None = field(default=None, init=False)

    def discover_capabilities(self) -> frozenset[BrokerCapability]:
        return self.adapter.metadata().capabilities

    def validate_configuration(self) -> MarketWorkerValidationResult:
        metadata = self.adapter.metadata()
        errors: list[str] = []

        if metadata.market != self.settings.market:
            errors.append("adapter market does not match worker market")
        if metadata.environment != self.settings.environment:
            errors.append("adapter environment does not match worker environment")
        if (
            self.settings.environment == BrokerEnvironment.LIVE
            and metadata.safety_policy.allow_live_execution
            and not self.settings.allow_live_execution
        ):
            errors.append("live execution requires explicit worker approval")
        if not metadata.safety_policy.require_market_arming:
            errors.append("adapter must require explicit market arming")
        if BrokerCapability.RECONCILE not in metadata.capabilities:
            errors.append("adapter must support reconciliation")
        if BrokerCapability.HEALTH_CHECK not in metadata.capabilities:
            errors.append("adapter must support health checks")
        configuration_errors = getattr(self.adapter, "configuration_errors", None)
        if callable(configuration_errors):
            errors.extend(str(item) for item in configuration_errors())

        result = MarketWorkerValidationResult(
            market=self.settings.market,
            valid=not errors,
            errors=tuple(errors),
        )
        self._last_validated_at = result.validated_at
        return result

    def _merge_validation_result(
        self,
        result: MarketWorkerValidationResult,
        extra_errors: list[str],
    ) -> MarketWorkerValidationResult:
        combined_errors = result.errors + tuple(extra_errors)
        merged = MarketWorkerValidationResult(
            market=result.market,
            valid=not combined_errors,
            errors=combined_errors,
        )
        self._last_validated_at = merged.validated_at
        return merged

    def health_check(self, *, max_age_seconds: int = 15) -> BrokerHealth:
        now = datetime.now(UTC)
        if (
            self._last_health is not None
            and self._last_health_check_at is not None
            and now - self._last_health_check_at <= timedelta(seconds=max_age_seconds)
        ):
            return self._last_health

        health = self.adapter.health_check()
        self._last_health = health
        self._last_health_check_at = health.checked_at
        return health

    def reconcile_portfolio(self, timeout_seconds: int | None = None) -> PortfolioSnapshot:
        reconciliation = self.adapter.reconcile(timeout_seconds=timeout_seconds)
        self._last_reconciled_at = reconciliation.completed_at
        return reconciliation.portfolio_snapshot(self.settings.market)

    def submit_order(self, order_request: OrderRequest) -> NormalizedOrder:
        return self.adapter.submit_order(order_request)

    def list_closed_trades(self, *, limit: int = 100) -> tuple[NormalizedTrade, ...]:
        return self.adapter.list_closed_trades(limit=limit)

    def status(self) -> MarketWorkerStatus:
        metadata = self.adapter.metadata()
        return MarketWorkerStatus(
            market=self.settings.market,
            environment=self.settings.environment,
            capabilities=metadata.capabilities,
            last_validated_at=self._last_validated_at,
            last_health_check_at=self._last_health_check_at,
            last_reconciled_at=self._last_reconciled_at,
        )