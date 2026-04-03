"""Application-layer adapter for runtime settings and safe default policies."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Protocol

from omnibot_v3.domain.api import (
    AuthPolicySettingsUpdateRequest,
    RuntimePolicySettingsUpdateRequest,
    SettingsResponse,
    SettingsUpdateRequest,
    build_settings_response,
    settings_response_to_dict,
)
from omnibot_v3.domain.config import AppConfig, AuthConfig


@dataclass(frozen=True, slots=True)
class SettingsSnapshot:
    config: AppConfig
    updated_at: datetime


class SettingsStore(Protocol):
    def load(self) -> SettingsSnapshot:
        """Return the latest stored settings snapshot."""

    def save(self, snapshot: SettingsSnapshot) -> None:
        """Persist the latest settings snapshot."""


@dataclass(frozen=True, slots=True)
class SettingsApiService:
    store: SettingsStore

    def get_config(self) -> AppConfig:
        return self.store.load().config

    def get_settings(self) -> SettingsResponse:
        snapshot = self.store.load()
        return build_settings_response(snapshot.config, updated_at=snapshot.updated_at)

    def get_settings_payload(self) -> dict[str, object]:
        return settings_response_to_dict(self.get_settings())

    def update_settings(
        self,
        request: SettingsUpdateRequest,
        *,
        now: datetime | None = None,
    ) -> SettingsResponse:
        current = self.store.load()
        updated_config = self._apply_update(current.config, request)
        updated_snapshot = SettingsSnapshot(
            config=updated_config,
            updated_at=now or datetime.now(UTC),
        )
        self.store.save(updated_snapshot)
        return build_settings_response(updated_config, updated_at=updated_snapshot.updated_at)

    def update_settings_payload(
        self,
        request: SettingsUpdateRequest,
        *,
        now: datetime | None = None,
    ) -> dict[str, object]:
        return settings_response_to_dict(self.update_settings(request, now=now))

    def _apply_update(self, config: AppConfig, request: SettingsUpdateRequest) -> AppConfig:
        runtime_update = request.runtime or RuntimePolicySettingsUpdateRequest()
        auth_update = request.auth or AuthPolicySettingsUpdateRequest()

        portfolio_snapshot_interval_seconds = self._positive_seconds(
            runtime_update.portfolio_snapshot_interval_seconds,
            field_name="portfolio_snapshot_interval_seconds",
            default=config.portfolio_snapshot_interval_seconds,
        )
        health_check_interval_seconds = self._positive_seconds(
            runtime_update.health_check_interval_seconds,
            field_name="health_check_interval_seconds",
            default=config.health_check_interval_seconds,
        )
        session_idle_timeout_seconds = self._positive_seconds(
            auth_update.session_idle_timeout_seconds,
            field_name="session_idle_timeout_seconds",
            default=config.auth.session_idle_timeout_seconds,
        )
        session_absolute_timeout_seconds = self._positive_seconds(
            auth_update.session_absolute_timeout_seconds,
            field_name="session_absolute_timeout_seconds",
            default=config.auth.session_absolute_timeout_seconds,
        )

        if session_absolute_timeout_seconds < session_idle_timeout_seconds:
            raise ValueError(
                "Maximum session length must be greater than or equal to the idle sign-out time."
            )

        auth_config = replace(
            config.auth,
            session_idle_timeout_seconds=session_idle_timeout_seconds,
            session_absolute_timeout_seconds=session_absolute_timeout_seconds,
            session_cookie_secure=(
                auth_update.session_cookie_secure
                if auth_update.session_cookie_secure is not None
                else config.auth.session_cookie_secure
            ),
            session_cookie_samesite=(
                auth_update.session_cookie_samesite
                if auth_update.session_cookie_samesite is not None
                else config.auth.session_cookie_samesite
            ),
            allowed_origin=self._resolve_allowed_origin(config.auth, auth_update),
        )

        return replace(
            config,
            log_level=(runtime_update.log_level if runtime_update.log_level is not None else config.log_level),
            broker_paper_trading=(
                runtime_update.broker_paper_trading
                if runtime_update.broker_paper_trading is not None
                else config.broker_paper_trading
            ),
            portfolio_snapshot_interval_seconds=portfolio_snapshot_interval_seconds,
            health_check_interval_seconds=health_check_interval_seconds,
            auth=auth_config,
        )

    def _positive_seconds(self, value: int | None, *, field_name: str, default: int) -> int:
        resolved = default if value is None else value
        if resolved < 1:
            raise ValueError(f"{self._field_label(field_name)} must be greater than 0.")
        return resolved

    def _resolve_allowed_origin(
        self,
        current: AuthConfig,
        update: AuthPolicySettingsUpdateRequest,
    ) -> str | None:
        if not update.allowed_origin_provided:
            return current.allowed_origin

        if update.allowed_origin is None or not update.allowed_origin.strip():
            return None

        normalized = update.allowed_origin.strip().rstrip("/")
        if not normalized.startswith(("http://", "https://")):
            raise ValueError("Allowed website must start with http:// or https://.")
        return normalized

    def _field_label(self, field_name: str) -> str:
        labels = {
            "portfolio_snapshot_interval_seconds": "Portfolio refresh interval",
            "health_check_interval_seconds": "Status check interval",
            "session_idle_timeout_seconds": "Idle sign-out time",
            "session_absolute_timeout_seconds": "Maximum session length",
        }
        return labels.get(field_name, field_name.replace("_", " "))