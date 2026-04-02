"""Dashboard and API smoke validation helpers for release readiness."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime

from fastapi.testclient import TestClient


@dataclass(frozen=True, slots=True)
class ApiSmokeCheck:
    name: str
    passed: bool
    status_code: int
    detail: str


@dataclass(frozen=True, slots=True)
class ApiSmokeReport:
    checked_at: str
    passed: bool
    check_count: int
    checks: tuple[ApiSmokeCheck, ...]


@dataclass(frozen=True, slots=True)
class ApiSmokeService:
    def run(self, client: TestClient, *, checked_at: datetime | None = None) -> ApiSmokeReport:
        checks: list[ApiSmokeCheck] = []

        unauthorized_runtime = client.get("/v1/runtime")
        checks.append(
            self._check(
                name="runtime-auth-required",
                response_status=unauthorized_runtime.status_code,
                passed=unauthorized_runtime.status_code == 401,
                detail="GET /v1/runtime rejects unauthenticated access.",
            )
        )

        login_response = client.post(
            "/v1/auth/login",
            json={"username": "admin", "password": "admin"},
        )
        checks.append(
            self._check(
                name="login",
                response_status=login_response.status_code,
                passed=login_response.status_code == 200,
                detail="POST /v1/auth/login returns an authenticated session.",
            )
        )

        if login_response.status_code != 200:
            return self._build_report(checks, checked_at=checked_at)

        login_payload = login_response.json()
        csrf_token = login_payload.get("csrf_token")
        session_response = client.get("/v1/auth/session")
        checks.append(
            self._check(
                name="session-view",
                response_status=session_response.status_code,
                passed=session_response.status_code == 200,
                detail="GET /v1/auth/session returns the current session view.",
            )
        )

        runtime_response = client.get("/v1/runtime")
        checks.append(
            self._check(
                name="runtime-overview",
                response_status=runtime_response.status_code,
                passed=runtime_response.status_code == 200,
                detail="GET /v1/runtime returns the aggregated runtime overview.",
            )
        )

        runtime_health_response = client.get("/v1/runtime/health")
        checks.append(
            self._check(
                name="runtime-health",
                response_status=runtime_health_response.status_code,
                passed=runtime_health_response.status_code == 200,
                detail="GET /v1/runtime/health returns runtime health details.",
            )
        )

        settings_response = client.get("/v1/settings")
        checks.append(
            self._check(
                name="settings-view",
                response_status=settings_response.status_code,
                passed=settings_response.status_code == 200,
                detail="GET /v1/settings returns safe runtime defaults and auth policy values.",
            )
        )

        market_validation_response = client.post(
            "/v1/markets/stocks/validate",
            headers=self._csrf_headers(csrf_token),
        )
        checks.append(
            self._check(
                name="market-validate",
                response_status=market_validation_response.status_code,
                passed=market_validation_response.status_code == 200,
                detail="POST /v1/markets/stocks/validate accepts a CSRF-protected validation request.",
            )
        )

        connect_market_response = client.post(
            "/v1/runtime/commands",
            json={"command": "connect-market", "market": "stocks"},
            headers=self._csrf_headers(csrf_token),
        )
        checks.append(
            self._check(
                name="market-connect",
                response_status=connect_market_response.status_code,
                passed=connect_market_response.status_code == 200,
                detail="POST /v1/runtime/commands can connect the stocks market.",
            )
        )

        reconcile_market_response = client.post(
            "/v1/markets/stocks/reconcile",
            headers=self._csrf_headers(csrf_token),
        )
        checks.append(
            self._check(
                name="market-reconcile",
                response_status=reconcile_market_response.status_code,
                passed=reconcile_market_response.status_code in {200, 400},
                detail="POST /v1/markets/stocks/reconcile is reachable and either reconciles or reports missing broker configuration.",
            )
        )

        portfolio_response = client.get("/v1/portfolio")
        checks.append(
            self._check(
                name="portfolio-overview",
                response_status=portfolio_response.status_code,
                passed=portfolio_response.status_code == 200,
                detail="GET /v1/portfolio returns an honest portfolio view, even when no broker credentials are configured yet.",
            )
        )

        analytics_response = client.get("/v1/portfolio/analytics")
        checks.append(
            self._check(
                name="portfolio-analytics",
                response_status=analytics_response.status_code,
                passed=analytics_response.status_code == 200,
                detail="GET /v1/portfolio/analytics returns an analytics payload even when the portfolio is still empty.",
            )
        )

        ui_state_response = client.get("/v1/ui/state")
        checks.append(
            self._check(
                name="ui-state",
                response_status=ui_state_response.status_code,
                passed=ui_state_response.status_code == 200,
                detail="GET /v1/ui/state returns dashboard-facing state payloads.",
            )
        )

        logout_response = client.post(
            "/v1/auth/logout",
            headers=self._csrf_headers(csrf_token),
        )
        checks.append(
            self._check(
                name="logout",
                response_status=logout_response.status_code,
                passed=logout_response.status_code == 200,
                detail="POST /v1/auth/logout invalidates the active session.",
            )
        )

        return self._build_report(checks, checked_at=checked_at)

    def report_to_dict(self, report: ApiSmokeReport) -> dict[str, object]:
        return {
            "checked_at": report.checked_at,
            "passed": report.passed,
            "check_count": report.check_count,
            "checks": [asdict(check) for check in report.checks],
        }

    def _build_report(
        self,
        checks: list[ApiSmokeCheck],
        *,
        checked_at: datetime | None,
    ) -> ApiSmokeReport:
        timestamp = checked_at or datetime.now(UTC)
        return ApiSmokeReport(
            checked_at=timestamp.isoformat(),
            passed=all(check.passed for check in checks),
            check_count=len(checks),
            checks=tuple(checks),
        )

    def _check(self, *, name: str, response_status: int, passed: bool, detail: str) -> ApiSmokeCheck:
        return ApiSmokeCheck(
            name=name,
            passed=passed,
            status_code=response_status,
            detail=detail,
        )

    def _csrf_headers(self, csrf_token: object) -> dict[str, str]:
        if not isinstance(csrf_token, str) or not csrf_token:
            return {}
        return {"X-CSRF-Token": csrf_token}