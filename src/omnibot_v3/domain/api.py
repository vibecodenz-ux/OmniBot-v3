"""Transport-agnostic API contracts for the future dashboard and HTTP layer."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from omnibot_v3.domain.broker import PortfolioSnapshot
from omnibot_v3.domain.config import AppConfig, CookieSameSite, LogLevel
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
from omnibot_v3.domain.health import RuntimeHealthReport
from omnibot_v3.domain.runtime import Market, MarketRuntimeSnapshot
from omnibot_v3.domain.worker import MarketWorkerValidationResult


class ApiCommandType(StrEnum):
    CONNECT_MARKET = "connect-market"
    DISCONNECT_MARKET = "disconnect-market"
    ARM_MARKET = "arm-market"
    DISARM_MARKET = "disarm-market"
    START_MARKET = "start-market"
    STOP_MARKET = "stop-market"
    RECONCILE_MARKET = "reconcile-market"
    COMPLETE_MARKET_RECONCILIATION = "complete-market-reconciliation"
    ENGAGE_KILL_SWITCH = "engage-kill-switch"
    RELEASE_KILL_SWITCH = "release-kill-switch"
    MARK_MARKET_ERROR = "mark-market-error"
    EMERGENCY_DISARM_ALL = "emergency-disarm-all"
    RECOVER_RUNTIME = "recover-runtime"
    GRACEFUL_SHUTDOWN_ALL = "graceful-shutdown-all"


@dataclass(frozen=True, slots=True)
class MarketSnapshotResponse:
    market: str
    state: str
    kill_switch_engaged: bool
    reconciliation_pending: bool
    last_reconciled_at: str | None
    last_error: str | None
    updated_at: str


@dataclass(frozen=True, slots=True)
class MarketHealthResponse:
    market: str
    state: str
    ready: bool
    reason: str


@dataclass(frozen=True, slots=True)
class RuntimeOverviewResponse:
    state: str
    ready: bool
    checked_at: str
    markets: tuple[MarketSnapshotResponse, ...]
    health: tuple[MarketHealthResponse, ...]


@dataclass(frozen=True, slots=True)
class RuntimeHealthSummaryResponse:
    state: str
    ready: bool
    checked_at: str
    market_reports: tuple[MarketHealthResponse, ...]


@dataclass(frozen=True, slots=True)
class RuntimeCommandRequest:
    command: ApiCommandType
    market: Market | None = None
    reason: str | None = None
    message: str | None = None


@dataclass(frozen=True, slots=True)
class RuntimeEventResponse:
    event_type: str
    market: str
    occurred_at: str
    reason: str | None = None
    previous_state: str | None = None
    new_state: str | None = None


@dataclass(frozen=True, slots=True)
class RuntimeCommandResponse:
    command: str
    event_count: int
    events: tuple[RuntimeEventResponse, ...]


@dataclass(frozen=True, slots=True)
class MarketValidationResponse:
    market: str
    valid: bool
    errors: tuple[str, ...]
    validated_at: str


@dataclass(frozen=True, slots=True)
class MarketControlResponse:
    market: str
    command: str
    event_count: int
    events: tuple[RuntimeEventResponse, ...]


@dataclass(frozen=True, slots=True)
class MarketReconciliationResponse:
    market: str
    command: str
    event_count: int
    events: tuple[RuntimeEventResponse, ...]
    reconciled_at: str


@dataclass(frozen=True, slots=True)
class PortfolioMarketWidgetResponse:
    market: str
    account_id: str
    currency: str
    equity: str
    cash: str
    buying_power: str
    market_value: str
    unrealized_pnl: str
    realized_pnl: str
    total_portfolio_value: str
    position_count: int
    open_order_count: int
    as_of: str


@dataclass(frozen=True, slots=True)
class PortfolioOverviewResponse:
    snapshot_count: int
    generated_at: str
    total_equity: str
    total_cash: str
    total_buying_power: str
    total_market_value: str
    total_unrealized_pnl: str
    total_realized_pnl: str
    total_portfolio_value: str
    total_position_count: int
    total_open_order_count: int
    markets: tuple[PortfolioMarketWidgetResponse, ...]


@dataclass(frozen=True, slots=True)
class PortfolioSnapshotProvenanceResponse:
    market: str
    account_id: str
    as_of: str
    source: str


@dataclass(frozen=True, slots=True)
class PortfolioAnalyticsProvenanceResponse:
    source_type: str
    aggregation: str
    snapshots: tuple[PortfolioSnapshotProvenanceResponse, ...]


@dataclass(frozen=True, slots=True)
class PortfolioMetricResponse:
    metric_id: str
    label: str
    value: str
    unit: str
    provenance: PortfolioAnalyticsProvenanceResponse


@dataclass(frozen=True, slots=True)
class PortfolioChartPointResponse:
    label: str
    value: str


@dataclass(frozen=True, slots=True)
class PortfolioChartResponse:
    chart_id: str
    title: str
    unit: str
    points: tuple[PortfolioChartPointResponse, ...]
    provenance: PortfolioAnalyticsProvenanceResponse


@dataclass(frozen=True, slots=True)
class PortfolioAnalyticsResponse:
    snapshot_count: int
    generated_at: str
    stats: tuple[PortfolioMetricResponse, ...]
    charts: tuple[PortfolioChartResponse, ...]


@dataclass(frozen=True, slots=True)
class UiBannerResponse:
    level: str
    title: str
    message: str


@dataclass(frozen=True, slots=True)
class UiMarketStateResponse:
    market: str
    state: str
    level: str
    title: str
    message: str
    reasons: tuple[str, ...]
    recommended_actions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class UiWidgetStateResponse:
    widget_id: str
    state: str
    level: str
    title: str
    message: str


@dataclass(frozen=True, slots=True)
class UiStateResponse:
    overall_state: str
    checked_at: str
    banner: UiBannerResponse
    markets: tuple[UiMarketStateResponse, ...]
    widgets: tuple[UiWidgetStateResponse, ...]


@dataclass(frozen=True, slots=True)
class RuntimePolicySettingsResponse:
    log_level: str
    broker_paper_trading: bool
    portfolio_snapshot_interval_seconds: int
    health_check_interval_seconds: int


@dataclass(frozen=True, slots=True)
class AuthPolicySettingsResponse:
    admin_username: str
    session_idle_timeout_seconds: int
    session_absolute_timeout_seconds: int
    session_cookie_secure: bool
    session_cookie_samesite: str
    allowed_origin: str | None


@dataclass(frozen=True, slots=True)
class SettingsResponse:
    environment: str
    updated_at: str
    runtime: RuntimePolicySettingsResponse
    auth: AuthPolicySettingsResponse


@dataclass(frozen=True, slots=True)
class RuntimePolicySettingsUpdateRequest:
    log_level: LogLevel | None = None
    broker_paper_trading: bool | None = None
    portfolio_snapshot_interval_seconds: int | None = None
    health_check_interval_seconds: int | None = None


@dataclass(frozen=True, slots=True)
class AuthPolicySettingsUpdateRequest:
    session_idle_timeout_seconds: int | None = None
    session_absolute_timeout_seconds: int | None = None
    session_cookie_secure: bool | None = None
    session_cookie_samesite: CookieSameSite | None = None
    allowed_origin: str | None = None
    allowed_origin_provided: bool = False


@dataclass(frozen=True, slots=True)
class SettingsUpdateRequest:
    runtime: RuntimePolicySettingsUpdateRequest | None = None
    auth: AuthPolicySettingsUpdateRequest | None = None


def snapshot_response_from_domain(snapshot: MarketRuntimeSnapshot) -> MarketSnapshotResponse:
    return MarketSnapshotResponse(
        market=snapshot.market.value,
        state=snapshot.state.value,
        kill_switch_engaged=snapshot.kill_switch_engaged,
        reconciliation_pending=snapshot.reconciliation_pending,
        last_reconciled_at=_timestamp_or_none(snapshot.last_reconciled_at),
        last_error=snapshot.last_error,
        updated_at=snapshot.updated_at.isoformat(),
    )


def health_response_from_domain(report: RuntimeHealthReport) -> tuple[MarketHealthResponse, ...]:
    return tuple(
        MarketHealthResponse(
            market=market_report.market.value,
            state=market_report.state.value,
            ready=market_report.ready,
            reason=market_report.reason,
        )
        for market_report in report.market_reports
    )


def build_runtime_overview_response(
    snapshots: dict[Market, MarketRuntimeSnapshot],
    health_report: RuntimeHealthReport,
) -> RuntimeOverviewResponse:
    ordered_markets = tuple(
        snapshot_response_from_domain(snapshots[market])
        for market in (Market.STOCKS, Market.CRYPTO, Market.FOREX)
    )
    return RuntimeOverviewResponse(
        state=health_report.state.value,
        ready=health_report.ready,
        checked_at=health_report.checked_at.isoformat(),
        markets=ordered_markets,
        health=health_response_from_domain(health_report),
    )


def build_runtime_health_summary_response(
    health_report: RuntimeHealthReport,
) -> RuntimeHealthSummaryResponse:
    return RuntimeHealthSummaryResponse(
        state=health_report.state.value,
        ready=health_report.ready,
        checked_at=health_report.checked_at.isoformat(),
        market_reports=health_response_from_domain(health_report),
    )


def runtime_overview_response_to_dict(response: RuntimeOverviewResponse) -> dict[str, object]:
    return {
        "state": response.state,
        "ready": response.ready,
        "checked_at": response.checked_at,
        "markets": [asdict(market) for market in response.markets],
        "health": [asdict(market_health) for market_health in response.health],
    }


def runtime_health_summary_response_to_dict(
    response: RuntimeHealthSummaryResponse,
) -> dict[str, object]:
    return {
        "state": response.state,
        "ready": response.ready,
        "checked_at": response.checked_at,
        "market_reports": [asdict(market_health) for market_health in response.market_reports],
    }


def runtime_event_response_from_domain(event: RuntimeEvent) -> RuntimeEventResponse:
    if isinstance(event, (MarketStateTransitioned, MarketKillSwitchEngaged)):
        return RuntimeEventResponse(
            event_type=type(event).__name__,
            market=event.market.value,
            occurred_at=event.occurred_at.isoformat(),
            reason=event.reason,
            previous_state=event.previous_state.value,
            new_state=event.new_state.value,
        )
    if isinstance(
        event,
        (
            MarketReconciliationRequested,
            MarketReconciliationCompleted,
            MarketKillSwitchReleased,
        ),
    ):
        return RuntimeEventResponse(
            event_type=type(event).__name__,
            market=event.market.value,
            occurred_at=event.occurred_at.isoformat(),
            reason=event.reason,
        )
    raise ValueError(f"Unsupported runtime event type: {type(event).__name__}")


def build_runtime_command_response(
    request: RuntimeCommandRequest,
    events: list[RuntimeEvent],
) -> RuntimeCommandResponse:
    event_payloads = tuple(runtime_event_response_from_domain(event) for event in events)
    return RuntimeCommandResponse(
        command=request.command.value,
        event_count=len(event_payloads),
        events=event_payloads,
    )


def runtime_command_response_to_dict(response: RuntimeCommandResponse) -> dict[str, object]:
    return {
        "command": response.command,
        "event_count": response.event_count,
        "events": [asdict(event) for event in response.events],
    }


def build_market_validation_response(
    result: MarketWorkerValidationResult,
) -> MarketValidationResponse:
    return MarketValidationResponse(
        market=result.market.value,
        valid=result.valid,
        errors=result.errors,
        validated_at=result.validated_at.isoformat(),
    )


def market_validation_response_to_dict(response: MarketValidationResponse) -> dict[str, object]:
    return {
        "market": response.market,
        "valid": response.valid,
        "errors": list(response.errors),
        "validated_at": response.validated_at,
    }


def build_market_control_response(
    market: Market,
    response: RuntimeCommandResponse,
) -> MarketControlResponse:
    return MarketControlResponse(
        market=market.value,
        command=response.command,
        event_count=response.event_count,
        events=response.events,
    )


def market_control_response_to_dict(response: MarketControlResponse) -> dict[str, object]:
    return {
        "market": response.market,
        "command": response.command,
        "event_count": response.event_count,
        "events": [asdict(event) for event in response.events],
    }


def build_market_reconciliation_response(
    market: Market,
    response: RuntimeCommandResponse,
    reconciled_at: datetime,
) -> MarketReconciliationResponse:
    return MarketReconciliationResponse(
        market=market.value,
        command=response.command,
        event_count=response.event_count,
        events=response.events,
        reconciled_at=reconciled_at.isoformat(),
    )


def market_reconciliation_response_to_dict(
    response: MarketReconciliationResponse,
) -> dict[str, object]:
    return {
        "market": response.market,
        "command": response.command,
        "event_count": response.event_count,
        "events": [asdict(event) for event in response.events],
        "reconciled_at": response.reconciled_at,
    }


def build_portfolio_market_widget_response(
    snapshot: PortfolioSnapshot,
) -> PortfolioMarketWidgetResponse:
    return PortfolioMarketWidgetResponse(
        market=snapshot.market.value,
        account_id=snapshot.account.account_id,
        currency=snapshot.account.currency,
        equity=_decimal_to_string(snapshot.account.equity),
        cash=_decimal_to_string(snapshot.account.cash),
        buying_power=_decimal_to_string(snapshot.account.buying_power),
        market_value=_decimal_to_string(snapshot.total_market_value),
        unrealized_pnl=_decimal_to_string(snapshot.total_unrealized_pnl),
        realized_pnl=_decimal_to_string(snapshot.total_realized_pnl),
        total_portfolio_value=_decimal_to_string(snapshot.total_portfolio_value),
        position_count=len(snapshot.positions),
        open_order_count=snapshot.open_order_count,
        as_of=snapshot.as_of.isoformat(),
    )


def build_portfolio_overview_response(
    snapshots: dict[Market, PortfolioSnapshot],
    *,
    generated_at: datetime,
) -> PortfolioOverviewResponse:
    ordered_snapshots = tuple(
        snapshots[market]
        for market in (Market.STOCKS, Market.CRYPTO, Market.FOREX)
        if market in snapshots
    )
    markets = tuple(build_portfolio_market_widget_response(snapshot) for snapshot in ordered_snapshots)
    return PortfolioOverviewResponse(
        snapshot_count=len(markets),
        generated_at=generated_at.isoformat(),
        total_equity=_decimal_to_string(
            sum((snapshot.account.equity for snapshot in ordered_snapshots), Decimal("0"))
        ),
        total_cash=_decimal_to_string(
            sum((snapshot.account.cash for snapshot in ordered_snapshots), Decimal("0"))
        ),
        total_buying_power=_decimal_to_string(
            sum((snapshot.account.buying_power for snapshot in ordered_snapshots), Decimal("0"))
        ),
        total_market_value=_decimal_to_string(
            sum((snapshot.total_market_value for snapshot in ordered_snapshots), Decimal("0"))
        ),
        total_unrealized_pnl=_decimal_to_string(
            sum((snapshot.total_unrealized_pnl for snapshot in ordered_snapshots), Decimal("0"))
        ),
        total_realized_pnl=_decimal_to_string(
            sum((snapshot.total_realized_pnl for snapshot in ordered_snapshots), Decimal("0"))
        ),
        total_portfolio_value=_decimal_to_string(
            sum((snapshot.total_portfolio_value for snapshot in ordered_snapshots), Decimal("0"))
        ),
        total_position_count=sum(len(snapshot.positions) for snapshot in ordered_snapshots),
        total_open_order_count=sum(snapshot.open_order_count for snapshot in ordered_snapshots),
        markets=markets,
    )


def portfolio_overview_response_to_dict(response: PortfolioOverviewResponse) -> dict[str, object]:
    return {
        "snapshot_count": response.snapshot_count,
        "generated_at": response.generated_at,
        "total_equity": response.total_equity,
        "total_cash": response.total_cash,
        "total_buying_power": response.total_buying_power,
        "total_market_value": response.total_market_value,
        "total_unrealized_pnl": response.total_unrealized_pnl,
        "total_realized_pnl": response.total_realized_pnl,
        "total_portfolio_value": response.total_portfolio_value,
        "total_position_count": response.total_position_count,
        "total_open_order_count": response.total_open_order_count,
        "markets": [asdict(market) for market in response.markets],
    }


def build_portfolio_analytics_response(
    snapshots: dict[Market, PortfolioSnapshot],
    *,
    generated_at: datetime,
) -> PortfolioAnalyticsResponse:
    ordered_snapshots = tuple(
        snapshots[market]
        for market in (Market.STOCKS, Market.CRYPTO, Market.FOREX)
        if market in snapshots
    )
    return PortfolioAnalyticsResponse(
        snapshot_count=len(ordered_snapshots),
        generated_at=generated_at.isoformat(),
        stats=(
            _build_portfolio_metric_response(
                metric_id="total-portfolio-value",
                label="Total Portfolio Value",
                value=sum(
                    (snapshot.total_portfolio_value for snapshot in ordered_snapshots),
                    Decimal("0"),
                ),
                aggregation="sum(latest.total_portfolio_value)",
                snapshots=ordered_snapshots,
            ),
            _build_portfolio_metric_response(
                metric_id="total-equity",
                label="Total Equity",
                value=sum((snapshot.account.equity for snapshot in ordered_snapshots), Decimal("0")),
                aggregation="sum(latest.account.equity)",
                snapshots=ordered_snapshots,
            ),
            _build_portfolio_metric_response(
                metric_id="total-unrealized-pnl",
                label="Total Unrealized PnL",
                value=sum(
                    (snapshot.total_unrealized_pnl for snapshot in ordered_snapshots),
                    Decimal("0"),
                ),
                aggregation="sum(latest.total_unrealized_pnl)",
                snapshots=ordered_snapshots,
            ),
            _build_portfolio_metric_response(
                metric_id="total-realized-pnl",
                label="Total Realized PnL",
                value=sum(
                    (snapshot.total_realized_pnl for snapshot in ordered_snapshots),
                    Decimal("0"),
                ),
                aggregation="sum(latest.total_realized_pnl)",
                snapshots=ordered_snapshots,
            ),
        ),
        charts=(
            _build_portfolio_chart_response(
                chart_id="market-value-by-market",
                title="Market Value by Market",
                unit="currency",
                aggregation="per_market(latest.total_market_value)",
                points=tuple(
                    PortfolioChartPointResponse(
                        label=snapshot.market.value,
                        value=_decimal_to_string(snapshot.total_market_value),
                    )
                    for snapshot in ordered_snapshots
                ),
                snapshots=ordered_snapshots,
            ),
            _build_portfolio_chart_response(
                chart_id="unrealized-pnl-by-market",
                title="Unrealized PnL by Market",
                unit="currency",
                aggregation="per_market(latest.total_unrealized_pnl)",
                points=tuple(
                    PortfolioChartPointResponse(
                        label=snapshot.market.value,
                        value=_decimal_to_string(snapshot.total_unrealized_pnl),
                    )
                    for snapshot in ordered_snapshots
                ),
                snapshots=ordered_snapshots,
            ),
            _build_portfolio_chart_response(
                chart_id="open-orders-by-market",
                title="Open Orders by Market",
                unit="count",
                aggregation="per_market(latest.open_order_count)",
                points=tuple(
                    PortfolioChartPointResponse(
                        label=snapshot.market.value,
                        value=str(snapshot.open_order_count),
                    )
                    for snapshot in ordered_snapshots
                ),
                snapshots=ordered_snapshots,
            ),
        ),
    )


def portfolio_analytics_response_to_dict(response: PortfolioAnalyticsResponse) -> dict[str, object]:
    return {
        "snapshot_count": response.snapshot_count,
        "generated_at": response.generated_at,
        "stats": [asdict(stat) for stat in response.stats],
        "charts": [asdict(chart) for chart in response.charts],
    }


def build_ui_state_response(
    *,
    runtime: RuntimeOverviewResponse,
    health: RuntimeHealthSummaryResponse,
    portfolio: PortfolioOverviewResponse,
    analytics: PortfolioAnalyticsResponse,
) -> UiStateResponse:
    return UiStateResponse(
        overall_state=runtime.state,
        checked_at=health.checked_at,
        banner=_build_ui_banner_response(runtime, health, portfolio, analytics),
        markets=tuple(
            _build_ui_market_state_response(snapshot, market_health)
            for snapshot, market_health in zip(runtime.markets, health.market_reports, strict=True)
        ),
        widgets=(
            _build_ui_widget_state_response(
                widget_id="portfolio-overview",
                snapshot_count=portfolio.snapshot_count,
                title="Portfolio Overview",
            ),
            _build_ui_widget_state_response(
                widget_id="portfolio-analytics",
                snapshot_count=analytics.snapshot_count,
                title="Portfolio Analytics",
            ),
        ),
    )


def ui_state_response_to_dict(response: UiStateResponse) -> dict[str, object]:
    return {
        "overall_state": response.overall_state,
        "checked_at": response.checked_at,
        "banner": asdict(response.banner),
        "markets": [asdict(market) for market in response.markets],
        "widgets": [asdict(widget) for widget in response.widgets],
    }


def build_settings_response(config: AppConfig, *, updated_at: datetime) -> SettingsResponse:
    return SettingsResponse(
        environment=config.environment.value,
        updated_at=updated_at.isoformat(),
        runtime=RuntimePolicySettingsResponse(
            log_level=config.log_level.value,
            broker_paper_trading=config.broker_paper_trading,
            portfolio_snapshot_interval_seconds=config.portfolio_snapshot_interval_seconds,
            health_check_interval_seconds=config.health_check_interval_seconds,
        ),
        auth=AuthPolicySettingsResponse(
            admin_username=config.auth.admin_username,
            session_idle_timeout_seconds=config.auth.session_idle_timeout_seconds,
            session_absolute_timeout_seconds=config.auth.session_absolute_timeout_seconds,
            session_cookie_secure=config.auth.session_cookie_secure,
            session_cookie_samesite=config.auth.session_cookie_samesite.value,
            allowed_origin=config.auth.allowed_origin,
        ),
    )


def settings_response_to_dict(response: SettingsResponse) -> dict[str, object]:
    return {
        "environment": response.environment,
        "updated_at": response.updated_at,
        "runtime": asdict(response.runtime),
        "auth": asdict(response.auth),
    }


def runtime_command_request_to_domain(
    request: RuntimeCommandRequest,
) -> RuntimeCommand | EmergencyDisarmAll | RecoverRuntime | GracefulShutdownAll:
    market = request.market

    if request.command == ApiCommandType.CONNECT_MARKET:
        if market is None:
            raise ValueError(f"Command {request.command.value} requires a market.")
        return ConnectMarket(market=market)
    if request.command == ApiCommandType.DISCONNECT_MARKET:
        if market is None:
            raise ValueError(f"Command {request.command.value} requires a market.")
        return DisconnectMarket(market=market)
    if request.command == ApiCommandType.ARM_MARKET:
        if market is None:
            raise ValueError(f"Command {request.command.value} requires a market.")
        return ArmMarket(market=market)
    if request.command == ApiCommandType.DISARM_MARKET:
        if market is None:
            raise ValueError(f"Command {request.command.value} requires a market.")
        return DisarmMarket(market=market)
    if request.command == ApiCommandType.START_MARKET:
        if market is None:
            raise ValueError(f"Command {request.command.value} requires a market.")
        return StartMarket(market=market)
    if request.command == ApiCommandType.STOP_MARKET:
        if market is None:
            raise ValueError(f"Command {request.command.value} requires a market.")
        return StopMarket(market=market)
    if request.command == ApiCommandType.RECONCILE_MARKET:
        if market is None:
            raise ValueError(f"Command {request.command.value} requires a market.")
        return ReconcileMarket(market=market)
    if request.command == ApiCommandType.COMPLETE_MARKET_RECONCILIATION:
        if market is None:
            raise ValueError(f"Command {request.command.value} requires a market.")
        return CompleteMarketReconciliation(market=market)
    if request.command == ApiCommandType.ENGAGE_KILL_SWITCH:
        if market is None:
            raise ValueError(f"Command {request.command.value} requires a market.")
        return EngageKillSwitch(market=market, reason=request.reason or "market kill switch engaged")
    if request.command == ApiCommandType.RELEASE_KILL_SWITCH:
        if market is None:
            raise ValueError(f"Command {request.command.value} requires a market.")
        return ReleaseKillSwitch(market=market, reason=request.reason or "market kill switch released")
    if request.command == ApiCommandType.MARK_MARKET_ERROR:
        if market is None:
            raise ValueError(f"Command {request.command.value} requires a market.")
        if not request.message:
            raise ValueError("Command mark-market-error requires a message.")
        return MarkMarketError(market=market, message=request.message)
    if request.command == ApiCommandType.EMERGENCY_DISARM_ALL:
        if market is not None:
            raise ValueError(f"Command {request.command.value} does not accept a market.")
        return EmergencyDisarmAll()
    if request.command == ApiCommandType.RECOVER_RUNTIME:
        if market is not None:
            raise ValueError(f"Command {request.command.value} does not accept a market.")
        return RecoverRuntime()
    if request.command == ApiCommandType.GRACEFUL_SHUTDOWN_ALL:
        if market is not None:
            raise ValueError(f"Command {request.command.value} does not accept a market.")
        return GracefulShutdownAll()

    raise ValueError(f"Unsupported API command type: {request.command.value}")


def _timestamp_or_none(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _decimal_to_string(value: Decimal) -> str:
    return format(value, "f")


def _build_portfolio_metric_response(
    *,
    metric_id: str,
    label: str,
    value: Decimal,
    aggregation: str,
    snapshots: tuple[PortfolioSnapshot, ...],
) -> PortfolioMetricResponse:
    return PortfolioMetricResponse(
        metric_id=metric_id,
        label=label,
        value=_decimal_to_string(value),
        unit="currency",
        provenance=_build_portfolio_analytics_provenance(aggregation, snapshots),
    )


def _build_portfolio_chart_response(
    *,
    chart_id: str,
    title: str,
    unit: str,
    aggregation: str,
    points: tuple[PortfolioChartPointResponse, ...],
    snapshots: tuple[PortfolioSnapshot, ...],
) -> PortfolioChartResponse:
    return PortfolioChartResponse(
        chart_id=chart_id,
        title=title,
        unit=unit,
        points=points,
        provenance=_build_portfolio_analytics_provenance(aggregation, snapshots),
    )


def _build_portfolio_analytics_provenance(
    aggregation: str,
    snapshots: tuple[PortfolioSnapshot, ...],
) -> PortfolioAnalyticsProvenanceResponse:
    return PortfolioAnalyticsProvenanceResponse(
        source_type="stored-latest-portfolio-snapshot",
        aggregation=aggregation,
        snapshots=tuple(
            PortfolioSnapshotProvenanceResponse(
                market=snapshot.market.value,
                account_id=snapshot.account.account_id,
                as_of=snapshot.as_of.isoformat(),
                source="portfolio_snapshot_store.latest",
            )
            for snapshot in snapshots
        ),
    )


def _build_ui_banner_response(
    runtime: RuntimeOverviewResponse,
    health: RuntimeHealthSummaryResponse,
    portfolio: PortfolioOverviewResponse,
    analytics: PortfolioAnalyticsResponse,
) -> UiBannerResponse:
    if runtime.state == "unhealthy":
        return UiBannerResponse(
            level="error",
            title="Runtime Attention Required",
            message="At least one market is in an unhealthy or error state.",
        )
    if runtime.state == "degraded":
        if _all_not_ready_markets_are_not_started(health):
            return UiBannerResponse(
                level="warning",
                title="Runtime Idle",
                message=(
                    f"Broker connections are healthy, but {_count_not_ready_markets(health)} market(s) are not started; "
                    f"{portfolio.snapshot_count} portfolio snapshot(s) and "
                    f"{analytics.snapshot_count} analytics source snapshot(s) are available."
                ),
            )
        return UiBannerResponse(
            level="warning",
            title="Runtime Degraded",
            message=(
                f"{_count_not_ready_markets(health)} market(s) need attention; "
                f"{portfolio.snapshot_count} portfolio snapshot(s) and "
                f"{analytics.snapshot_count} analytics source snapshot(s) are available."
            ),
        )
    return UiBannerResponse(
        level="success",
        title="Runtime Ready",
        message="All markets are healthy and the dashboard data surface is available.",
    )


def _build_ui_market_state_response(
    snapshot: MarketSnapshotResponse,
    market_health: MarketHealthResponse,
) -> UiMarketStateResponse:
    reasons = tuple(reason.strip() for reason in market_health.reason.split(",") if reason.strip())
    if snapshot.state == "ERROR":
        message = snapshot.last_error or market_health.reason
        return UiMarketStateResponse(
            market=snapshot.market,
            state="error",
            level="error",
            title="Market Error",
            message=message,
            reasons=reasons or (message,),
            recommended_actions=("review runtime audit", "reconnect market", "inspect broker health"),
        )
    if snapshot.state == "DISCONNECTED":
        return UiMarketStateResponse(
            market=snapshot.market,
            state="idle",
            level="warning",
            title="Market Not Started",
            message="Broker connectivity may be healthy, but this market runtime is not started and cannot trade until you press Start.",
            reasons=reasons or ("market runtime is not started",),
            recommended_actions=("start market", "validate broker status", "reconcile market"),
        )
    if market_health.state != "healthy":
        return UiMarketStateResponse(
            market=snapshot.market,
            state="degraded",
            level="warning",
            title="Market Degraded",
            message=market_health.reason,
            reasons=reasons,
            recommended_actions=("review health details", "reconcile market", "inspect runtime audit"),
        )
    return UiMarketStateResponse(
        market=snapshot.market,
        state="ready",
        level="success",
        title="Market Ready",
        message="Market is ready for normal dashboard operation.",
        reasons=(market_health.reason,),
        recommended_actions=("monitor market",),
    )


def _build_ui_widget_state_response(
    *,
    widget_id: str,
    snapshot_count: int,
    title: str,
) -> UiWidgetStateResponse:
    if snapshot_count == 0:
        return UiWidgetStateResponse(
            widget_id=widget_id,
            state="empty",
            level="warning",
            title=title,
            message="No stored portfolio snapshots are available yet.",
        )
    return UiWidgetStateResponse(
        widget_id=widget_id,
        state="ready",
        level="success",
        title=title,
        message="Stored snapshot-backed data is available.",
    )


def _all_not_ready_markets_are_not_started(health: RuntimeHealthSummaryResponse) -> bool:
    reports = tuple(report for report in health.market_reports if not report.ready)
    if not reports:
        return False
    return all(report.reason.strip() == "market runtime is not started" for report in reports)


def _count_not_ready_markets(health: RuntimeHealthSummaryResponse) -> int:
    return sum(1 for market in health.market_reports if not market.ready)