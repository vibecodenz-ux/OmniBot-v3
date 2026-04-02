"""In-memory login audit store for development and tests."""

from __future__ import annotations

from dataclasses import dataclass, field

from omnibot_v3.domain.auth import LoginAuditEvent
from omnibot_v3.services.login_audit import LoginAuditStore


@dataclass(slots=True)
class InMemoryLoginAuditStore(LoginAuditStore):
    events: list[LoginAuditEvent] = field(default_factory=list)

    def append(self, events: list[LoginAuditEvent]) -> None:
        self.events.extend(events)

    def list_events(self) -> list[LoginAuditEvent]:
        return list(self.events)