"""FastAPI adapter for OmniBot v3 runtime endpoints."""

from __future__ import annotations

import os
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from omnibot_v3 import __version__
from omnibot_v3.domain import (
    ApiCommandType,
    CookieSameSite,
    InvalidStateTransitionError,
    LoginOutcome,
    LogLevel,
    Market,
    OmnibotEnvironment,
    RuntimeCommandRequest,
    SecretScope,
    load_config,
)
from omnibot_v3.domain.api import (
    AuthPolicySettingsUpdateRequest,
    RuntimePolicySettingsUpdateRequest,
    SettingsUpdateRequest,
    build_ui_state_response,
    portfolio_analytics_response_to_dict,
    portfolio_overview_response_to_dict,
    runtime_health_summary_response_to_dict,
    runtime_overview_response_to_dict,
    ui_state_response_to_dict,
)
from omnibot_v3.infra import (
    InMemoryLoginAuditStore,
    InMemorySecretRegistry,
    InMemorySessionStore,
    JsonFileSecretRegistry,
)
from omnibot_v3.infra.market_data_store import (
    InMemoryHistoricalBarStore,
    JsonFileHistoricalBarStore,
)
from omnibot_v3.infra.operator_state_store import JsonFileOperatorStateStore
from omnibot_v3.infra.runtime_store import (
    InMemoryPortfolioSnapshotStore,
    JsonFilePortfolioSnapshotStore,
)
from omnibot_v3.infra.settings_store import InMemorySettingsStore
from omnibot_v3.services import (
    AuditApiService,
    AuthenticationError,
    CsrfValidationError,
    LoginAuditService,
    RuntimeApiService,
    SecretApiService,
    SecretNotFoundError,
    SecretRotationService,
    SecretStoreService,
    SessionAuthService,
    SessionPolicy,
    build_configured_market_workers,
    build_default_orchestrator,
)
from omnibot_v3.services.market_hours import MarketHoursService
from omnibot_v3.services.operator_state import OperatorStateService, OperatorStateStore
from omnibot_v3.services.session_auth import is_loopback_origin
from omnibot_v3.services.settings_api import SettingsApiService
from omnibot_v3.services.strategy_scanner import StrategyScannerService
from omnibot_v3.services.trade_journal import TradeJournalService
from omnibot_v3.services.trading_modules import TradingModuleService
from omnibot_v3.services.update_manager import UpdateApplyError, UpdateCheckError, UpdateManager


class LoginBody(BaseModel):
    username: str
    password: str
    request_id: str | None = None


class RuntimeCommandBody(BaseModel):
    command: ApiCommandType
    market: Market | None = None
    reason: str | None = None
    message: str | None = None

    def to_domain(self) -> RuntimeCommandRequest:
        return RuntimeCommandRequest(
            command=self.command,
            market=self.market,
            reason=self.reason,
            message=self.message,
        )


class UpsertSecretBody(BaseModel):
    scope: SecretScope
    value: str
    reference: str | None = None
    validate_after_store: bool = True


class RotateSecretBody(BaseModel):
    new_value: str
    new_reference: str | None = None
    validate_after_rotation: bool = True


class UpdateRuntimeSettingsBody(BaseModel):
    log_level: LogLevel | None = None
    broker_paper_trading: bool | None = None
    portfolio_snapshot_interval_seconds: int | None = None
    health_check_interval_seconds: int | None = None

    def to_domain(self) -> RuntimePolicySettingsUpdateRequest:
        return RuntimePolicySettingsUpdateRequest(
            log_level=self.log_level,
            broker_paper_trading=self.broker_paper_trading,
            portfolio_snapshot_interval_seconds=self.portfolio_snapshot_interval_seconds,
            health_check_interval_seconds=self.health_check_interval_seconds,
        )


class UpdateAuthSettingsBody(BaseModel):
    session_idle_timeout_seconds: int | None = None
    session_absolute_timeout_seconds: int | None = None
    session_cookie_secure: bool | None = None
    session_cookie_samesite: CookieSameSite | None = None
    allowed_origin: str | None = None

    def to_domain(self) -> AuthPolicySettingsUpdateRequest:
        fields_set = self.model_fields_set if hasattr(self, "model_fields_set") else self.__fields_set__
        return AuthPolicySettingsUpdateRequest(
            session_idle_timeout_seconds=self.session_idle_timeout_seconds,
            session_absolute_timeout_seconds=self.session_absolute_timeout_seconds,
            session_cookie_secure=self.session_cookie_secure,
            session_cookie_samesite=self.session_cookie_samesite,
            allowed_origin=self.allowed_origin,
            allowed_origin_provided="allowed_origin" in fields_set,
        )


class UpdateSettingsBody(BaseModel):
    runtime: UpdateRuntimeSettingsBody | None = None
    auth: UpdateAuthSettingsBody | None = None

    def to_domain(self) -> SettingsUpdateRequest:
        return SettingsUpdateRequest(
            runtime=self.runtime.to_domain() if self.runtime is not None else None,
            auth=self.auth.to_domain() if self.auth is not None else None,
        )


class UpdateDashboardPasswordBody(BaseModel):
    current_password: str
    new_password: str


class UpdateTradingModuleSelectionBody(BaseModel):
    profile_id: str | None = None


class ClosePositionBody(BaseModel):
    market: Market
    symbol: str


class BackfillTradeHistoryBody(BaseModel):
    market: Market | None = None
    limit: int = 100


@dataclass(slots=True)
class RuntimeApiAppDependencies:
    service: RuntimeApiService
    audit_service: AuditApiService
    secret_service: SecretApiService
    auth_service: SessionAuthService
    settings_service: SettingsApiService
    operator_state_service: OperatorStateService
    trading_module_service: TradingModuleService
    trade_journal_service: TradeJournalService
    market_hours_service: MarketHoursService
    update_manager: UpdateManager


def create_app(
    service: RuntimeApiService | None = None,
    auth_service: SessionAuthService | None = None,
    operator_state_store: OperatorStateStore | None = None,
) -> FastAPI:
    config = load_config()
    repo_root = Path(__file__).resolve().parents[3]
    app_root = Path.cwd()
    frontend_dist_root = repo_root / "frontend" / "dist"
    frontend_assets_root = frontend_dist_root / "assets"
    if not frontend_dist_root.exists() or not frontend_assets_root.exists():
        raise RuntimeError(
            "Missing frontend build output under frontend/dist. "
            "Run python scripts/ensure_frontend_build.py or bash scripts/run_dashboard.sh before starting the dashboard."
        )
    resolved_operator_state_store = operator_state_store or JsonFileOperatorStateStore(
        Path(config.data_root) / "operator-state.json"
    )
    operator_state_service = OperatorStateService(store=resolved_operator_state_store)
    owns_runtime_service = service is None
    secret_registry = (
        JsonFileSecretRegistry(Path(config.data_root) / "secret-metadata.json")
        if owns_runtime_service
        else InMemorySecretRegistry()
    )
    secret_store_service = SecretStoreService(
        environment=dict(os.environ),
        root_directory=app_root,
    )
    portfolio_store = (
        JsonFilePortfolioSnapshotStore(Path(config.data_root) / "portfolio-snapshots.json")
        if owns_runtime_service
        else InMemoryPortfolioSnapshotStore()
    )
    historical_bar_store = (
        JsonFileHistoricalBarStore(Path(config.data_root) / "historical-bars.json")
        if owns_runtime_service
        else InMemoryHistoricalBarStore()
    )
    runtime_service = service or RuntimeApiService(
        orchestrator=build_default_orchestrator(),
        workers=build_configured_market_workers(
            config=config,
            registry=secret_registry,
            store_service=secret_store_service,
        ),
        portfolio_store=portfolio_store,
        auto_reconcile_portfolio_reads=True,
    )
    resolved_auth_service = auth_service or SessionAuthService(
        admin_password=_default_admin_password(config.environment),
        store=InMemorySessionStore(),
        login_audit_service=LoginAuditService(store=InMemoryLoginAuditStore()),
        policy=SessionPolicy.from_config(config.auth),
    )
    persisted_password_hash = operator_state_service.get_admin_password_hash()
    if persisted_password_hash is not None:
        resolved_auth_service = resolved_auth_service.with_admin_password_hash(persisted_password_hash)
    settings_service = SettingsApiService(store=InMemorySettingsStore(config=config))

    trading_module_service = TradingModuleService(
        workers=runtime_service.workers,
        operator_state_service=operator_state_service,
    )
    trading_module_service.health_provider = runtime_service.get_market_health
    strategy_scanner_kwargs = {
        "orchestrator": runtime_service.orchestrator,
        "workers": runtime_service.workers,
        "portfolio_store": runtime_service.portfolio_store,
        "selection_provider": trading_module_service.current_selection,
        "historical_bar_store": historical_bar_store,
    }
    strategy_scanner = _build_strategy_scanner(
        operator_state_service=operator_state_service,
        **strategy_scanner_kwargs,
    )
    runtime_service.on_market_started = strategy_scanner.start_market
    trading_module_service.activity_provider = strategy_scanner.activity_payload
    for market in runtime_service.workers:
        strategy_scanner.start_market(market)

    dependencies = RuntimeApiAppDependencies(
        service=runtime_service,
        audit_service=AuditApiService(
            orchestrator=runtime_service.orchestrator,
            login_audit_service=resolved_auth_service.login_audit_service,
        ),
        secret_service=SecretApiService(
            registry=secret_registry,
            store_service=secret_store_service,
            rotation_service=SecretRotationService(store_service=secret_store_service),
        ),
        auth_service=resolved_auth_service,
        settings_service=settings_service,
        operator_state_service=operator_state_service,
        trading_module_service=trading_module_service,
        trade_journal_service=TradeJournalService(
            portfolio_store=runtime_service.portfolio_store,
            workers=runtime_service.workers,
            operator_state_service=operator_state_service,
            thesis_provider=getattr(strategy_scanner, "selected_thesis_for", None),
        ),
        market_hours_service=MarketHoursService(),
        update_manager=UpdateManager(
            repo_root=repo_root,
            config=config,
        ),
    )

    def refresh_configured_workers() -> None:
        if not owns_runtime_service:
            return
        updated_workers = build_configured_market_workers(
            config=dependencies.settings_service.get_config(),
            registry=secret_registry,
            store_service=secret_store_service,
        )
        dependencies.service.workers.clear()
        dependencies.service.workers.update(updated_workers)
        dependencies.trading_module_service.workers.clear()
        dependencies.trading_module_service.workers.update(updated_workers)
        strategy_scanner.workers.clear()
        strategy_scanner.workers.update(updated_workers)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        try:
            yield
        finally:
            strategy_scanner.stop_all()

    app = FastAPI(title="OmniBot v3 API", version=__version__, lifespan=lifespan)

    @app.middleware("http")
    async def disable_dashboard_caching(request: Request, call_next):
        response = await call_next(request)
        if request.url.path == "/" or request.url.path.startswith("/assets/"):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response

    app.mount("/assets", StaticFiles(directory=str(frontend_assets_root)), name="assets")

    def require_session(request: Request, *, require_csrf: bool = False):
        try:
            session = dependencies.auth_service.authenticate(
                request.cookies.get(dependencies.auth_service.policy.session_cookie_name),
                user_agent=request.headers.get("user-agent"),
            )
            if require_csrf:
                expected_origin = _csrf_expected_origins(
                    configured_allowed_origin=dependencies.auth_service.policy.allowed_origin,
                    request=request,
                    environment=dependencies.settings_service.get_config().environment,
                )
                dependencies.auth_service.validate_csrf(
                    session,
                    csrf_token=request.headers.get(
                        dependencies.auth_service.policy.csrf_header_name
                    ),
                    origin=request.headers.get("origin"),
                    referer=request.headers.get("referer"),
                    expected_origin=expected_origin,
                )
        except AuthenticationError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except CsrfValidationError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        return session

    @app.get("/", include_in_schema=False)
    def get_dashboard_shell() -> FileResponse:
        return FileResponse(frontend_dist_root / "index.html")

    @app.post("/v1/auth/login")
    def post_login(body: LoginBody, request: Request) -> JSONResponse:
        try:
            session = dependencies.auth_service.login(
                username=body.username,
                password=body.password,
                ip_address=_client_ip(request),
                user_agent=request.headers.get("user-agent"),
                request_id=body.request_id,
            )
        except AuthenticationError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc

        response = JSONResponse(dependencies.auth_service.session_view(session))
        response.set_cookie(
            key=dependencies.auth_service.policy.session_cookie_name,
            value=session.session_id,
            httponly=True,
            secure=dependencies.auth_service.policy.session_cookie_secure,
            samesite=dependencies.auth_service.policy.session_cookie_samesite.value,
            path="/",
        )
        return response

    @app.get("/v1/auth/session")
    def get_session(request: Request) -> dict[str, object]:
        session = require_session(request)
        return dependencies.auth_service.session_view(session)

    @app.post("/v1/auth/logout")
    def post_logout(request: Request) -> JSONResponse:
        require_session(request, require_csrf=True)
        dependencies.auth_service.logout(
            request.cookies.get(dependencies.auth_service.policy.session_cookie_name)
        )
        response = JSONResponse({"logged_out": True})
        response.delete_cookie(
            key=dependencies.auth_service.policy.session_cookie_name,
            path="/",
        )
        return response

    @app.get("/v1/runtime")
    def get_runtime(request: Request) -> dict[str, object]:
        require_session(request)
        return dependencies.service.get_runtime_overview_payload()

    @app.get("/v1/dashboard")
    def get_dashboard(request: Request) -> dict[str, object]:
        require_session(request)
        if dependencies.service.auto_reconcile_portfolio_reads:
            dependencies.service.synchronize_portfolios()

        runtime = dependencies.service.get_runtime_overview()
        health = dependencies.service.get_runtime_health()
        portfolio = dependencies.service.get_portfolio_overview(sync_portfolios=False)
        analytics = dependencies.service.get_portfolio_analytics(sync_portfolios=False)
        ui_state = build_ui_state_response(
            runtime=runtime,
            health=health,
            portfolio=portfolio,
            analytics=analytics,
        )

        return {
            "runtime": runtime_overview_response_to_dict(runtime),
            "health": runtime_health_summary_response_to_dict(health),
            "ui_state": ui_state_response_to_dict(ui_state),
            "portfolio": portfolio_overview_response_to_dict(portfolio),
            "analytics": portfolio_analytics_response_to_dict(analytics),
            "build": dependencies.update_manager.get_build_payload(),
            "settings": dependencies.settings_service.get_settings_payload(),
            "runtime_audit": dependencies.audit_service.get_runtime_audit_payload(),
            "login_audit": dependencies.audit_service.get_login_audit_payload(),
            "secrets": dependencies.secret_service.list_secret_metadata(),
            "modules": dependencies.trading_module_service.list_modules_payload(),
            "journal": dependencies.trade_journal_service.get_journal_payload(),
            "market_hours": dependencies.market_hours_service.get_payload(),
            "strategy_activity": strategy_scanner.decision_log_payload(),
        }

    @app.get("/v1/system/build")
    def get_build_info(request: Request) -> dict[str, object]:
        require_session(request)
        return dependencies.update_manager.get_build_payload()

    @app.post("/v1/system/update/check")
    def post_update_check(request: Request) -> dict[str, object]:
        require_session(request, require_csrf=True)
        try:
            return dependencies.update_manager.check_for_updates()
        except UpdateCheckError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.post("/v1/system/update/apply")
    def post_update_apply(request: Request) -> dict[str, object]:
        require_session(request, require_csrf=True)
        current_config = dependencies.settings_service.get_config()
        try:
            payload = dependencies.update_manager.schedule_update(
                bind_host=current_config.dashboard_host,
                port=current_config.dashboard_port,
            )
        except UpdateCheckError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except UpdateApplyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        dependencies.auth_service.store.delete_all()
        return payload

    @app.get("/v1/system/update/status")
    def get_update_status(request: Request) -> dict[str, object]:
        require_session(request)
        return dependencies.update_manager.get_update_status_payload()

    @app.post("/v1/system/update/rollback")
    def post_update_rollback(request: Request) -> dict[str, object]:
        require_session(request, require_csrf=True)
        current_config = dependencies.settings_service.get_config()
        try:
            payload = dependencies.update_manager.schedule_rollback(
                bind_host=current_config.dashboard_host,
                port=current_config.dashboard_port,
            )
        except UpdateApplyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        dependencies.auth_service.store.delete_all()
        return payload

    @app.get("/v1/runtime/health")
    def get_runtime_health(request: Request) -> dict[str, object]:
        require_session(request)
        return dependencies.service.get_runtime_health_payload()

    @app.get("/v1/portfolio")
    def get_portfolio_overview(request: Request) -> dict[str, object]:
        require_session(request)
        return dependencies.service.get_portfolio_overview_payload()

    @app.get("/v1/portfolio/analytics")
    def get_portfolio_analytics(request: Request) -> dict[str, object]:
        require_session(request)
        return dependencies.service.get_portfolio_analytics_payload()

    @app.get("/v1/trading/modules")
    def get_trading_modules(request: Request) -> dict[str, object]:
        require_session(request)
        return dependencies.trading_module_service.list_modules_payload()

    @app.put("/v1/trading/modules/{market}/selection")
    def put_trading_module_selection(
        market: Market,
        body: UpdateTradingModuleSelectionBody,
        request: Request,
    ) -> dict[str, object]:
        require_session(request, require_csrf=True)
        try:
            return dependencies.trading_module_service.update_selection_payload(
                market=market,
                profile_id=body.profile_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/v1/trades/journal")
    def get_trade_journal(request: Request) -> dict[str, object]:
        require_session(request)
        if dependencies.service.auto_reconcile_portfolio_reads:
            dependencies.service.synchronize_portfolios()
        return dependencies.trade_journal_service.get_journal_payload()

    @app.post("/v1/trades/close-position")
    def post_close_position(body: ClosePositionBody, request: Request) -> dict[str, object]:
        require_session(request, require_csrf=True)
        try:
            return dependencies.trade_journal_service.close_position_payload(
                market=body.market,
                symbol=body.symbol,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/v1/trades/backfill-history")
    def post_backfill_trade_history(body: BackfillTradeHistoryBody, request: Request) -> dict[str, object]:
        require_session(request, require_csrf=True)
        try:
            return dependencies.trade_journal_service.backfill_closed_trades_payload(
                market=body.market,
                limit=body.limit,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/v1/trades/clear-history")
    def post_clear_trade_history(request: Request) -> dict[str, object]:
        require_session(request, require_csrf=True)
        try:
            return dependencies.trade_journal_service.clear_closed_trade_history_payload()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/v1/ui/state")
    def get_ui_state(request: Request) -> dict[str, object]:
        require_session(request)
        return dependencies.service.get_ui_state_payload()

    @app.get("/v1/settings")
    def get_settings(request: Request) -> dict[str, object]:
        require_session(request)
        return dependencies.settings_service.get_settings_payload()

    @app.put("/v1/settings")
    def put_settings(body: UpdateSettingsBody, request: Request) -> dict[str, object]:
        require_session(request, require_csrf=True)
        payload = dependencies.settings_service.update_settings_payload(body.to_domain())
        dependencies.auth_service = dependencies.auth_service.with_policy(
            SessionPolicy.from_config(dependencies.settings_service.get_config().auth)
        )
        refresh_configured_workers()
        return payload

    @app.post("/v1/settings/dashboard-password")
    def post_dashboard_password(
        body: UpdateDashboardPasswordBody,
        request: Request,
    ) -> dict[str, object]:
        session = require_session(request, require_csrf=True)
        if not dependencies.auth_service.verify_admin_password(body.current_password):
            raise HTTPException(status_code=401, detail="current password is invalid")

        new_password = body.new_password.strip()
        if len(new_password) < 8:
            raise HTTPException(status_code=400, detail="new password must be at least 8 characters")
        if dependencies.auth_service.verify_admin_password(new_password):
            raise HTTPException(status_code=400, detail="new password must be different from the current password")

        dependencies.auth_service = dependencies.auth_service.with_admin_password(new_password)
        dependencies.operator_state_service.update_admin_password_hash(
            dependencies.auth_service.current_admin_password_hash()
        )
        return {
            "updated": True,
            "actor_id": session.actor_id,
            "message": "Dashboard password updated successfully.",
        }

    @app.post("/v1/markets/{market}/validate")
    def post_validate_market(market: Market, request: Request) -> dict[str, object]:
        require_session(request, require_csrf=True)
        try:
            return dependencies.service.validate_market_payload(market)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/v1/markets/{market}/arm")
    def post_arm_market(market: Market, request: Request) -> dict[str, object]:
        require_session(request, require_csrf=True)
        return _handle_market_control(lambda: dependencies.service.arm_market_payload(market))

    @app.post("/v1/markets/{market}/disarm")
    def post_disarm_market(market: Market, request: Request) -> dict[str, object]:
        require_session(request, require_csrf=True)
        return _handle_market_control(lambda: dependencies.service.disarm_market_payload(market))

    @app.post("/v1/markets/{market}/start")
    def post_start_market(market: Market, request: Request) -> dict[str, object]:
        require_session(request, require_csrf=True)
        return _handle_market_control(lambda: dependencies.service.start_market_payload(market))

    @app.post("/v1/markets/{market}/stop")
    def post_stop_market(market: Market, request: Request) -> dict[str, object]:
        require_session(request, require_csrf=True)
        return _handle_market_control(lambda: dependencies.service.stop_market_payload(market))

    @app.post("/v1/markets/{market}/reconcile")
    def post_reconcile_market(market: Market, request: Request) -> dict[str, object]:
        require_session(request, require_csrf=True)
        return _handle_market_control(lambda: dependencies.service.reconcile_market_payload(market))

    @app.get("/v1/audit/runtime")
    def get_runtime_audit(request: Request, market: Market | None = None) -> dict[str, object]:
        require_session(request)
        return dependencies.audit_service.get_runtime_audit_payload(market=market)

    @app.get("/v1/audit/logins")
    def get_login_audit(
        request: Request,
        actor_id: str | None = None,
        outcome: LoginOutcome | None = None,
    ) -> dict[str, object]:
        require_session(request)
        return dependencies.audit_service.get_login_audit_payload(
            actor_id=actor_id,
            outcome=outcome,
        )

    @app.get("/v1/secrets")
    def get_secrets(request: Request, scope: SecretScope | None = None) -> dict[str, object]:
        require_session(request)
        return dependencies.secret_service.list_secret_metadata(scope=scope)

    @app.get("/v1/secrets/{secret_id}")
    def get_secret(secret_id: str, request: Request) -> dict[str, object]:
        require_session(request)
        try:
            return dependencies.secret_service.get_secret_metadata(secret_id)
        except SecretNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"Secret '{secret_id}' not found") from exc

    @app.put("/v1/secrets/{secret_id}")
    def put_secret(secret_id: str, body: UpsertSecretBody, request: Request) -> dict[str, object]:
        require_session(request, require_csrf=True)
        payload = dependencies.secret_service.upsert_secret(
            secret_id=secret_id,
            scope=body.scope,
            value=body.value,
            reference=body.reference,
            validate_after_store=body.validate_after_store,
        )
        refresh_configured_workers()
        return payload

    @app.post("/v1/secrets/{secret_id}/validate")
    def post_validate_secret(secret_id: str, request: Request) -> dict[str, object]:
        require_session(request, require_csrf=True)
        try:
            return dependencies.secret_service.validate_secret(secret_id)
        except SecretNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"Secret '{secret_id}' not found") from exc

    @app.post("/v1/secrets/{secret_id}/rotate")
    def post_rotate_secret(
        secret_id: str,
        body: RotateSecretBody,
        request: Request,
    ) -> dict[str, object]:
        require_session(request, require_csrf=True)
        try:
            payload = dependencies.secret_service.rotate_secret(
                secret_id=secret_id,
                new_value=body.new_value,
                new_reference=body.new_reference,
                validate_after_rotation=body.validate_after_rotation,
            )
            refresh_configured_workers()
            return payload
        except SecretNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"Secret '{secret_id}' not found") from exc

    @app.delete("/v1/secrets/{secret_id}")
    def delete_secret(secret_id: str, request: Request) -> dict[str, object]:
        require_session(request, require_csrf=True)
        try:
            payload = dependencies.secret_service.revoke_secret(secret_id)
            refresh_configured_workers()
            return payload
        except SecretNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"Secret '{secret_id}' not found") from exc

    @app.post("/v1/runtime/commands")
    def post_runtime_command(body: RuntimeCommandBody, request: Request) -> dict[str, object]:
        require_session(request, require_csrf=True)
        return _handle_market_control(lambda: dependencies.service.execute_command_payload(body.to_domain()))

    def _handle_market_control(handler: Callable[[], dict[str, object]]) -> dict[str, object]:
        try:
            return handler()
        except InvalidStateTransitionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return app


def main() -> int:
    import uvicorn

    config = load_config()
    uvicorn.run(create_app(), host=config.dashboard_host, port=config.dashboard_port)
    return 0


def _build_strategy_scanner(
    *,
    orchestrator,
    workers,
    portfolio_store,
    selection_provider,
    historical_bar_store,
    operator_state_service: OperatorStateService,
) -> StrategyScannerService:
    return StrategyScannerService(
        orchestrator=orchestrator,
        workers=workers,
        portfolio_store=portfolio_store,
        selection_provider=selection_provider,
        operator_state_service=operator_state_service,
        historical_bar_store=historical_bar_store,
    )


def _default_admin_password(environment: OmnibotEnvironment) -> str:
    admin_password = os.environ.get("OMNIBOT_ADMIN_PASSWORD")
    if admin_password:
        return admin_password
    if environment in {OmnibotEnvironment.DEVELOPMENT, OmnibotEnvironment.CI}:
        return "admin"
    raise RuntimeError("OMNIBOT_ADMIN_PASSWORD must be set outside development and CI")


def _csrf_expected_origins(
    *,
    configured_allowed_origin: str | None,
    request: Request,
    environment: OmnibotEnvironment,
) -> str | tuple[str, ...]:
    origins = {str(request.base_url).rstrip("/")}
    if configured_allowed_origin:
        origins.add(configured_allowed_origin.rstrip("/"))
    elif environment in {OmnibotEnvironment.DEVELOPMENT, OmnibotEnvironment.CI}:
        request_origin = request.headers.get("origin")
        if is_loopback_origin(request_origin):
            origins.add(str(request_origin).rstrip("/"))
        referer = request.headers.get("referer")
        if referer:
            parsed_referer = urlparse(referer)
            referer_origin = f"{parsed_referer.scheme}://{parsed_referer.netloc}" if parsed_referer.scheme and parsed_referer.netloc else None
            if is_loopback_origin(referer_origin):
                origins.add(str(referer_origin).rstrip("/"))
    if len(origins) == 1:
        return next(iter(origins))
    return tuple(sorted(origins))


def _client_ip(request: Request) -> str:
    if request.client is None:
        return "unknown"
    return request.client.host


if __name__ == "__main__":
    raise SystemExit(main())