"""Service layer package for OmniBot v3."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from omnibot_v3.services.api_smoke import ApiSmokeService
    from omnibot_v3.services.audit_api import AuditApiService
    from omnibot_v3.services.broker_adapter import BrokerAdapter, BrokerAdapterContractHarness
    from omnibot_v3.services.data_catalog import DataCatalog
    from omnibot_v3.services.decision_engine import (
        ExecutionPlanner,
        ExitPlanner,
        ExplanationBuilder,
        LayeredStrategyPlugin,
        RegimeClassifier,
        SetupPlanner,
    )
    from omnibot_v3.services.linux_preflight import LinuxPreflightValidator
    from omnibot_v3.services.login_audit import LoginAuditService, LoginAuditStore
    from omnibot_v3.services.market_integrations import (
        CryptoWorker,
        ForexWorker,
        StocksWorker,
        build_configured_market_workers,
        build_default_market_workers,
    )
    from omnibot_v3.services.market_worker import MarketWorker
    from omnibot_v3.services.orchestrator import TradingOrchestrator
    from omnibot_v3.services.release_evidence import ReleaseEvidenceService
    from omnibot_v3.services.release_readiness import ReleaseReadinessService
    from omnibot_v3.services.risk_engine import RiskPolicyEngine, StrategyRuntime
    from omnibot_v3.services.runtime_api import RuntimeApiService
    from omnibot_v3.services.runtime_health import RuntimeHealthEvaluator
    from omnibot_v3.services.runtime_probe import RuntimeProbeService
    from omnibot_v3.services.runtime_store import RuntimeEventStore, RuntimeSnapshotStore
    from omnibot_v3.services.scanner_replay_validation import (
        ReplayValidationResult,
        ReplayValidationStep,
        ScannerReplayValidationService,
    )
    from omnibot_v3.services.secret_api import SecretApiService, SecretNotFoundError, SecretRegistry
    from omnibot_v3.services.secrets import (
        SecretAccessError,
        SecretPolicyService,
        SecretRotationRequest,
        SecretRotationResult,
        SecretRotationService,
        SecretRotationSummary,
        SecretStoreService,
    )
    from omnibot_v3.services.session_auth import (
        AuthenticationError,
        CsrfValidationError,
        SessionAuthService,
        SessionPolicy,
        SessionStore,
    )

__all__ = [
    "ApiSmokeService",
    "AuditApiService",
    "BrokerAdapter",
    "BrokerAdapterContractHarness",
    "CryptoWorker",
    "DataCatalog",
    "ExecutionPlanner",
    "ExplanationBuilder",
    "ExitPlanner",
    "ForexWorker",
    "LayeredStrategyPlugin",
    "LinuxPreflightValidator",
    "LoginAuditService",
    "LoginAuditStore",
    "MarketWorker",
    "RiskPolicyEngine",
    "ReleaseEvidenceService",
    "ReleaseReadinessService",
    "RegimeClassifier",
    "ReplayValidationResult",
    "ReplayValidationStep",
    "RuntimeApiService",
    "SecretAccessError",
    "SecretPolicyService",
    "SecretRotationRequest",
    "SecretRotationResult",
    "SecretRotationService",
    "SecretRotationSummary",
    "SecretStoreService",
    "RuntimeEventStore",
    "RuntimeHealthEvaluator",
    "RuntimeProbeService",
    "RuntimeSnapshotStore",
    "ScannerReplayValidationService",
    "SecretApiService",
    "AuthenticationError",
    "CsrfValidationError",
    "SecretNotFoundError",
    "SessionAuthService",
    "SessionPolicy",
    "SecretRegistry",
    "SessionStore",
    "SetupPlanner",
    "StocksWorker",
    "StrategyRuntime",
    "TradingOrchestrator",
    "build_configured_market_workers",
    "build_default_market_workers",
    "build_default_orchestrator",
]


def __getattr__(name: str) -> Any:
    if name == "ApiSmokeService":
        from omnibot_v3.services.api_smoke import ApiSmokeService

        return ApiSmokeService

    if name == "ReleaseReadinessService":
        from omnibot_v3.services.release_readiness import ReleaseReadinessService

        return ReleaseReadinessService

    if name == "AuditApiService":
        from omnibot_v3.services.audit_api import AuditApiService

        return AuditApiService

    if name == "ReleaseEvidenceService":
        from omnibot_v3.services.release_evidence import ReleaseEvidenceService

        return ReleaseEvidenceService

    if name == "DataCatalog":
        from omnibot_v3.services.data_catalog import DataCatalog

        return DataCatalog

    if name in {
        "ExecutionPlanner",
        "ExplanationBuilder",
        "ExitPlanner",
        "LayeredStrategyPlugin",
        "RegimeClassifier",
        "SetupPlanner",
    }:
        from omnibot_v3.services.decision_engine import (
            ExecutionPlanner,
            ExitPlanner,
            ExplanationBuilder,
            LayeredStrategyPlugin,
            RegimeClassifier,
            SetupPlanner,
        )

        decision_engine_exports: dict[str, Any] = {
            "ExecutionPlanner": ExecutionPlanner,
            "ExplanationBuilder": ExplanationBuilder,
            "ExitPlanner": ExitPlanner,
            "LayeredStrategyPlugin": LayeredStrategyPlugin,
            "RegimeClassifier": RegimeClassifier,
            "SetupPlanner": SetupPlanner,
        }
        return decision_engine_exports[name]

    if name in {"LoginAuditService", "LoginAuditStore"}:
        from omnibot_v3.services.login_audit import LoginAuditService, LoginAuditStore

        login_audit_exports: dict[str, Any] = {
            "LoginAuditService": LoginAuditService,
            "LoginAuditStore": LoginAuditStore,
        }
        return login_audit_exports[name]

    if name in {
        "SecretAccessError",
        "SecretPolicyService",
        "SecretRotationRequest",
        "SecretRotationResult",
        "SecretRotationService",
        "SecretRotationSummary",
        "SecretStoreService",
    }:
        from omnibot_v3.services.secrets import (
            SecretAccessError,
            SecretPolicyService,
            SecretRotationRequest,
            SecretRotationResult,
            SecretRotationService,
            SecretRotationSummary,
            SecretStoreService,
        )

        secret_exports: dict[str, Any] = {
            "SecretAccessError": SecretAccessError,
            "SecretPolicyService": SecretPolicyService,
            "SecretRotationRequest": SecretRotationRequest,
            "SecretRotationResult": SecretRotationResult,
            "SecretRotationService": SecretRotationService,
            "SecretRotationSummary": SecretRotationSummary,
            "SecretStoreService": SecretStoreService,
        }
        return secret_exports[name]

    if name in {"BrokerAdapter", "BrokerAdapterContractHarness"}:
        from omnibot_v3.services.broker_adapter import BrokerAdapter, BrokerAdapterContractHarness

        broker_exports: dict[str, Any] = {
            "BrokerAdapter": BrokerAdapter,
            "BrokerAdapterContractHarness": BrokerAdapterContractHarness,
        }
        return broker_exports[name]

    if name == "MarketWorker":
        from omnibot_v3.services.market_worker import MarketWorker

        return MarketWorker

    if name == "LinuxPreflightValidator":
        from omnibot_v3.services.linux_preflight import LinuxPreflightValidator

        return LinuxPreflightValidator

    if name in {"RiskPolicyEngine", "StrategyRuntime"}:
        from omnibot_v3.services.risk_engine import RiskPolicyEngine, StrategyRuntime

        risk_exports: dict[str, Any] = {
            "RiskPolicyEngine": RiskPolicyEngine,
            "StrategyRuntime": StrategyRuntime,
        }
        return risk_exports[name]

    if name == "RuntimeHealthEvaluator":
        from omnibot_v3.services.runtime_health import RuntimeHealthEvaluator

        return RuntimeHealthEvaluator

    if name == "RuntimeApiService":
        from omnibot_v3.services.runtime_api import RuntimeApiService

        return RuntimeApiService

    if name in {"ReplayValidationResult", "ReplayValidationStep", "ScannerReplayValidationService"}:
        from omnibot_v3.services.scanner_replay_validation import (
            ReplayValidationResult,
            ReplayValidationStep,
            ScannerReplayValidationService,
        )

        replay_exports: dict[str, Any] = {
            "ReplayValidationResult": ReplayValidationResult,
            "ReplayValidationStep": ReplayValidationStep,
            "ScannerReplayValidationService": ScannerReplayValidationService,
        }
        return replay_exports[name]

    if name in {
        "SecretApiService",
        "SecretNotFoundError",
        "SecretRegistry",
    }:
        from omnibot_v3.services.secret_api import (
            SecretApiService,
            SecretNotFoundError,
            SecretRegistry,
        )

        secret_api_exports: dict[str, Any] = {
            "SecretApiService": SecretApiService,
            "SecretNotFoundError": SecretNotFoundError,
            "SecretRegistry": SecretRegistry,
        }
        return secret_api_exports[name]

    if name in {
        "AuthenticationError",
        "CsrfValidationError",
        "SessionAuthService",
        "SessionPolicy",
        "SessionStore",
    }:
        from omnibot_v3.services.session_auth import (
            AuthenticationError,
            CsrfValidationError,
            SessionAuthService,
            SessionPolicy,
            SessionStore,
        )

        session_auth_exports: dict[str, Any] = {
            "AuthenticationError": AuthenticationError,
            "CsrfValidationError": CsrfValidationError,
            "SessionAuthService": SessionAuthService,
            "SessionPolicy": SessionPolicy,
            "SessionStore": SessionStore,
        }
        return session_auth_exports[name]

    if name == "RuntimeProbeService":
        from omnibot_v3.services.runtime_probe import RuntimeProbeService

        return RuntimeProbeService

    if name in {"StocksWorker", "CryptoWorker", "ForexWorker", "build_configured_market_workers", "build_default_market_workers"}:
        from omnibot_v3.services.market_integrations import (
            CryptoWorker,
            ForexWorker,
            StocksWorker,
            build_configured_market_workers,
            build_default_market_workers,
        )

        market_worker_exports: dict[str, Any] = {
            "StocksWorker": StocksWorker,
            "CryptoWorker": CryptoWorker,
            "ForexWorker": ForexWorker,
            "build_configured_market_workers": build_configured_market_workers,
            "build_default_market_workers": build_default_market_workers,
        }
        return market_worker_exports[name]

    if name in {"TradingOrchestrator", "build_default_orchestrator"}:
        from omnibot_v3.services.orchestrator import (
            TradingOrchestrator,
            build_default_orchestrator,
        )

        orchestrator_exports: dict[str, Any] = {
            "TradingOrchestrator": TradingOrchestrator,
            "build_default_orchestrator": build_default_orchestrator,
        }
        return orchestrator_exports[name]

    if name in {"RuntimeEventStore", "RuntimeSnapshotStore"}:
        from omnibot_v3.services.runtime_store import RuntimeEventStore, RuntimeSnapshotStore

        runtime_store_exports: dict[str, Any] = {
            "RuntimeEventStore": RuntimeEventStore,
            "RuntimeSnapshotStore": RuntimeSnapshotStore,
        }
        return runtime_store_exports[name]

    raise AttributeError(name)
