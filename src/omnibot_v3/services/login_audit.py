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
        title, subtitle = self._event_copy(event)
        return {
            "title": title,
            "subtitle": subtitle,
            "meta": self._event_meta(event),
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

    def _event_copy(self, event: LoginAuditEvent) -> tuple[str, str]:
        actor_label = event.actor.actor_id or "User"
        mechanism_label = event.mechanism.value.replace("_", " ").title()
        if event.outcome == LoginOutcome.SUCCESS:
            return (f"{actor_label} signed in", f"{mechanism_label} sign-in successful")

        failure_reason = self._format_failure_reason(event.failure_reason)
        return (f"{actor_label} sign-in failed", failure_reason or f"{mechanism_label} sign-in failed")

    def _event_meta(self, event: LoginAuditEvent) -> list[str]:
        meta: list[str] = []
        if event.context.ip_address:
            meta.append(f"IP: {event.context.ip_address}")
        return meta

    def _format_failure_reason(self, failure_reason: str | None) -> str | None:
        if failure_reason is None:
            return None
        if failure_reason == "incorrect username or password":
            return "Incorrect username or password."
        return failure_reason[:1].upper() + failure_reason[1:]