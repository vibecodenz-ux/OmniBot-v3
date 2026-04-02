"""Single-user session authentication and CSRF protection services."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from hashlib import pbkdf2_hmac
from hmac import compare_digest
from secrets import token_bytes, token_urlsafe
from typing import Protocol
from urllib.parse import urlparse

from omnibot_v3.domain.auth import (
    AuthenticatedSession,
    LoginMechanism,
    LoginOutcome,
)
from omnibot_v3.domain.config import AuthConfig, CookieSameSite
from omnibot_v3.services.login_audit import LoginAuditService

_PASSWORD_HASH_PREFIX = "pbkdf2_sha256"
_PASSWORD_HASH_ITERATIONS = 600_000


class AuthenticationError(PermissionError):
    """Raised when a request is unauthenticated or a session is invalid."""


class CsrfValidationError(PermissionError):
    """Raised when a state-changing request fails CSRF validation."""


class SessionStore(Protocol):
    def save(self, session: AuthenticatedSession) -> None:
        """Persist a session record."""

    def get(self, session_id: str) -> AuthenticatedSession | None:
        """Return a session record when it exists."""

    def delete(self, session_id: str) -> None:
        """Delete a session record."""

    def delete_all(self) -> None:
        """Delete all persisted session records."""


@dataclass(frozen=True, slots=True)
class SessionPolicy:
    admin_username: str = "admin"
    session_cookie_name: str = "omnibot_session"
    csrf_header_name: str = "X-CSRF-Token"
    session_idle_timeout_seconds: int = 900
    session_absolute_timeout_seconds: int = 28_800
    session_cookie_secure: bool = False
    session_cookie_samesite: CookieSameSite = CookieSameSite.STRICT
    allowed_origin: str | None = None

    @classmethod
    def from_config(cls, config: AuthConfig) -> SessionPolicy:
        return cls(
            admin_username=config.admin_username,
            session_cookie_name=config.session_cookie_name,
            csrf_header_name=config.csrf_header_name,
            session_idle_timeout_seconds=config.session_idle_timeout_seconds,
            session_absolute_timeout_seconds=config.session_absolute_timeout_seconds,
            session_cookie_secure=config.session_cookie_secure,
            session_cookie_samesite=config.session_cookie_samesite,
            allowed_origin=config.allowed_origin,
        )


@dataclass(frozen=True, slots=True)
class SessionAuthService:
    admin_password: str
    store: SessionStore
    login_audit_service: LoginAuditService
    policy: SessionPolicy = SessionPolicy()

    def __post_init__(self) -> None:
        if not _is_password_hash(self.admin_password):
            object.__setattr__(self, "admin_password", _hash_password(self.admin_password))

    def login(
        self,
        username: str,
        password: str,
        *,
        ip_address: str,
        user_agent: str | None = None,
        request_id: str | None = None,
        now: datetime | None = None,
    ) -> AuthenticatedSession:
        timestamp = now or datetime.now(UTC)
        if username != self.policy.admin_username or not self.verify_admin_password(password):
            self.login_audit_service.record_event(
                actor_id=username,
                principal=username,
                mechanism=LoginMechanism.PASSWORD,
                outcome=LoginOutcome.FAILURE,
                ip_address=ip_address,
                user_agent=user_agent,
                request_id=request_id,
                failure_reason="invalid credentials",
            )
            raise AuthenticationError("invalid credentials")

        self.store.delete_all()
        session = AuthenticatedSession(
            session_id=token_urlsafe(32),
            actor_id=self.policy.admin_username,
            principal=username,
            csrf_token=token_urlsafe(32),
            created_at=timestamp,
            last_seen_at=timestamp,
            user_agent=user_agent,
        )
        self.store.save(session)
        self.login_audit_service.record_event(
            actor_id=session.actor_id,
            principal=session.principal,
            mechanism=LoginMechanism.PASSWORD,
            outcome=LoginOutcome.SUCCESS,
            ip_address=ip_address,
            user_agent=user_agent,
            request_id=request_id,
        )
        return session

    def authenticate(
        self,
        session_id: str | None,
        *,
        user_agent: str | None = None,
        now: datetime | None = None,
    ) -> AuthenticatedSession:
        if not session_id:
            raise AuthenticationError("authentication required")

        session = self.store.get(session_id)
        if session is None:
            raise AuthenticationError("authentication required")

        timestamp = now or datetime.now(UTC)
        if self._is_expired(session, timestamp):
            self.store.delete(session.session_id)
            raise AuthenticationError("session expired")
        if session.user_agent is not None and user_agent is not None and session.user_agent != user_agent:
            self.store.delete(session.session_id)
            raise AuthenticationError("session fingerprint mismatch")

        updated = replace(session, last_seen_at=timestamp)
        self.store.save(updated)
        return updated

    def logout(self, session_id: str | None) -> None:
        if session_id:
            self.store.delete(session_id)

    def with_policy(self, policy: SessionPolicy) -> SessionAuthService:
        return replace(self, policy=policy)

    def verify_admin_password(self, password: str) -> bool:
        return _verify_password(password, self.admin_password)

    def with_admin_password(self, password: str) -> SessionAuthService:
        return replace(self, admin_password=_hash_password(password))

    def with_admin_password_hash(self, password_hash: str) -> SessionAuthService:
        return replace(self, admin_password=password_hash)

    def current_admin_password_hash(self) -> str:
        return self.admin_password

    def validate_csrf(
        self,
        session: AuthenticatedSession,
        *,
        csrf_token: str | None,
        origin: str | None,
        referer: str | None,
        expected_origin: str | tuple[str, ...],
    ) -> None:
        if not csrf_token or not compare_digest(csrf_token, session.csrf_token):
            raise CsrfValidationError("CSRF token validation failed")
        expected_origins = (
            tuple(item.rstrip("/") for item in expected_origin)
            if isinstance(expected_origin, tuple)
            else (expected_origin.rstrip("/"),)
        )
        if origin is not None and origin.rstrip("/") not in expected_origins:
            raise CsrfValidationError("request origin not allowed")
        if origin is None and referer is not None:
            normalized_referer = referer.rstrip("/")
            if not any(
                normalized_referer == allowed or normalized_referer.startswith(f"{allowed}/")
                for allowed in expected_origins
            ):
                raise CsrfValidationError("request referer not allowed")

    def session_view(self, session: AuthenticatedSession) -> dict[str, object]:
        return {
            "actor_id": session.actor_id,
            "principal": session.principal,
            "csrf_token": session.csrf_token,
            "created_at": session.created_at.isoformat(),
            "last_seen_at": session.last_seen_at.isoformat(),
            "idle_expires_at": (
                session.last_seen_at
                + timedelta(seconds=self.policy.session_idle_timeout_seconds)
            ).isoformat(),
            "absolute_expires_at": (
                session.created_at
                + timedelta(seconds=self.policy.session_absolute_timeout_seconds)
            ).isoformat(),
        }

    def _is_expired(self, session: AuthenticatedSession, now: datetime) -> bool:
        idle_expired = now - session.last_seen_at > timedelta(
            seconds=self.policy.session_idle_timeout_seconds
        )
        absolute_expired = now - session.created_at > timedelta(
            seconds=self.policy.session_absolute_timeout_seconds
        )
        return idle_expired or absolute_expired


def is_loopback_origin(origin: str | None) -> bool:
    if not origin:
        return False
    parsed = urlparse(origin)
    return parsed.scheme in {"http", "https"} and parsed.hostname in {"127.0.0.1", "localhost"}

    def session_view(self, session: AuthenticatedSession) -> dict[str, object]:
        return {
            "actor_id": session.actor_id,
            "principal": session.principal,
            "csrf_token": session.csrf_token,
            "created_at": session.created_at.isoformat(),
            "last_seen_at": session.last_seen_at.isoformat(),
            "idle_expires_at": (
                session.last_seen_at
                + timedelta(seconds=self.policy.session_idle_timeout_seconds)
            ).isoformat(),
            "absolute_expires_at": (
                session.created_at
                + timedelta(seconds=self.policy.session_absolute_timeout_seconds)
            ).isoformat(),
        }

    def _is_expired(self, session: AuthenticatedSession, now: datetime) -> bool:
        idle_expired = now - session.last_seen_at > timedelta(
            seconds=self.policy.session_idle_timeout_seconds
        )
        absolute_expired = now - session.created_at > timedelta(
            seconds=self.policy.session_absolute_timeout_seconds
        )
        return idle_expired or absolute_expired


def _is_password_hash(value: str) -> bool:
    return value.startswith(f"{_PASSWORD_HASH_PREFIX}$")


def _hash_password(password: str) -> str:
    salt = token_bytes(16)
    digest = pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PASSWORD_HASH_ITERATIONS)
    return (
        f"{_PASSWORD_HASH_PREFIX}${_PASSWORD_HASH_ITERATIONS}$"
        f"{salt.hex()}${digest.hex()}"
    )


def _verify_password(password: str, password_hash: str) -> bool:
    if not _is_password_hash(password_hash):
        return compare_digest(password, password_hash)

    algorithm, iterations, salt_hex, digest_hex = password_hash.split("$", maxsplit=3)
    if algorithm != _PASSWORD_HASH_PREFIX:
        return False

    derived = pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        bytes.fromhex(salt_hex),
        int(iterations),
    )
    return compare_digest(derived.hex(), digest_hex)