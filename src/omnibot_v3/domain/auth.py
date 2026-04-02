"""Authentication and login audit domain models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum


def utc_now() -> datetime:
    return datetime.now(UTC)


class LoginOutcome(StrEnum):
    SUCCESS = "success"
    FAILURE = "failure"
    LOCKED = "locked"
    CHALLENGE_REQUIRED = "challenge-required"


class LoginMechanism(StrEnum):
    PASSWORD = "password"
    TOKEN = "token"
    API_KEY = "api-key"
    RECOVERY = "recovery"


@dataclass(frozen=True, slots=True)
class LoginActor:
    actor_id: str
    principal: str


@dataclass(frozen=True, slots=True)
class LoginContext:
    ip_address: str
    user_agent: str | None = None
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class LoginAuditEvent:
    actor: LoginActor
    mechanism: LoginMechanism
    outcome: LoginOutcome
    context: LoginContext
    occurred_at: datetime = field(default_factory=utc_now)
    failure_reason: str | None = None


@dataclass(frozen=True, slots=True)
class AuthenticatedSession:
    session_id: str
    actor_id: str
    principal: str
    csrf_token: str
    created_at: datetime = field(default_factory=utc_now)
    last_seen_at: datetime = field(default_factory=utc_now)
    user_agent: str | None = None