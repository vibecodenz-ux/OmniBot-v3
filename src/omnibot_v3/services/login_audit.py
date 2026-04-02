"""Login audit trail service and persistence port."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from omnibot_v3.domain.auth import (
    LoginActor,
    LoginAuditEvent,
    LoginContext,
    LoginMechanism,
    LoginOutcome,
)
from omnibot_v3.services.secrets import SecretPolicyService


class LoginAuditStore(Protocol):
    def append(self, events: list[LoginAuditEvent]) -> None:
        """Persist login audit events."""

    def list_events(self) -> list[LoginAuditEvent]:
        """Return login audit events in append order."""


@dataclass(frozen=True, slots=True)
class LoginAuditService:
    store: LoginAuditStore
    secret_policy_service: SecretPolicyService = SecretPolicyService()

    def record_event(
        self,
        actor_id: str,
        principal: str,
        mechanism: LoginMechanism,
        outcome: LoginOutcome,
        *,
        ip_address: str,
        user_agent: str | None = None,
        request_id: str | None = None,
        failure_reason: str | None = None,
    ) -> LoginAuditEvent:
        event = LoginAuditEvent(
            actor=LoginActor(actor_id=actor_id, principal=principal),
            mechanism=mechanism,
            outcome=outcome,
            context=LoginContext(
                ip_address=ip_address,
                user_agent=user_agent,
                request_id=request_id,
            ),
            failure_reason=failure_reason,
        )
        self.store.append([event])
        return event

    def list_events(
        self,
        *,
        actor_id: str | None = None,
        outcome: LoginOutcome | None = None,
    ) -> list[LoginAuditEvent]:
        events = self.store.list_events()
        if actor_id is not None:
            events = [event for event in events if event.actor.actor_id == actor_id]
        if outcome is not None:
            events = [event for event in events if event.outcome == outcome]
        return events

    def event_view(self, event: LoginAuditEvent) -> dict[str, object]:
        return {
            "actor_id": event.actor.actor_id,
            "principal": self.secret_policy_service.policy.redacted_value,
            "mechanism": event.mechanism.value,
            "outcome": event.outcome.value,
            "ip_address": event.context.ip_address,
            "user_agent": event.context.user_agent,
            "request_id": event.context.request_id,
            "occurred_at": event.occurred_at.isoformat(),
            "failure_reason": event.failure_reason,
        }

    def summarize_failures(self, *, actor_id: str | None = None) -> dict[str, int]:
        failures = self.list_events(actor_id=actor_id, outcome=LoginOutcome.FAILURE)
        summary: dict[str, int] = {}
        for event in failures:
            reason = event.failure_reason or "unknown"
            summary[reason] = summary.get(reason, 0) + 1
        return summary