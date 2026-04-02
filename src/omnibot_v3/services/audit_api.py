"""Application-layer adapter for audit read views."""

from __future__ import annotations

from dataclasses import dataclass

from omnibot_v3.domain import LoginOutcome, Market, runtime_event_response_from_domain
from omnibot_v3.services.login_audit import LoginAuditService
from omnibot_v3.services.orchestrator import TradingOrchestrator


@dataclass(frozen=True, slots=True)
class AuditApiService:
    orchestrator: TradingOrchestrator
    login_audit_service: LoginAuditService

    def get_runtime_audit_payload(self, *, market: Market | None = None) -> dict[str, object]:
        events = self.orchestrator.audit_log
        if market is not None:
            events = [event for event in events if event.market == market]
        return {
            "event_count": len(events),
            "events": [
                {
                    "event_type": payload.event_type,
                    "market": payload.market,
                    "occurred_at": payload.occurred_at,
                    "reason": payload.reason,
                    "previous_state": payload.previous_state,
                    "new_state": payload.new_state,
                }
                for payload in (runtime_event_response_from_domain(event) for event in events)
            ],
        }

    def get_login_audit_payload(
        self,
        *,
        actor_id: str | None = None,
        outcome: LoginOutcome | None = None,
    ) -> dict[str, object]:
        events = self.login_audit_service.list_events(actor_id=actor_id, outcome=outcome)
        return {
            "event_count": len(events),
            "events": [self.login_audit_service.event_view(event) for event in events],
            "failure_summary": self.login_audit_service.summarize_failures(actor_id=actor_id),
        }