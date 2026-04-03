"""Application-layer adapter for audit read views."""

from __future__ import annotations

from dataclasses import dataclass

from omnibot_v3.domain import (
    LoginOutcome,
    Market,
    MarketKillSwitchEngaged,
    MarketKillSwitchReleased,
    MarketReconciliationCompleted,
    MarketReconciliationRequested,
    MarketStateTransitioned,
    runtime_event_response_from_domain,
)
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
            "events": [self._runtime_event_view(event) for event in events],
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

    def _runtime_event_view(self, event: object) -> dict[str, object]:
        payload = runtime_event_response_from_domain(event)
        title, subtitle = self._runtime_event_copy(event, payload.market)
        meta = [
            value
            for value in (
                self._runtime_state_change(payload.previous_state, payload.new_state),
                self._format_reason(payload.reason),
            )
            if value is not None
        ]
        return {
            "title": title,
            "subtitle": subtitle,
            "meta": meta,
            "event_type": payload.event_type,
            "market": payload.market,
            "occurred_at": payload.occurred_at,
            "reason": payload.reason,
            "previous_state": payload.previous_state,
            "new_state": payload.new_state,
        }

    def _runtime_event_copy(self, event: object, market: str) -> tuple[str, str]:
        market_label = market.title()
        if isinstance(event, MarketStateTransitioned):
            return (f"{market_label} updated", "Market status changed.")
        if isinstance(event, MarketKillSwitchEngaged):
            return (f"{market_label} paused", "Safety stop turned on.")
        if isinstance(event, MarketKillSwitchReleased):
            return (f"{market_label} resumed", "Safety stop turned off.")
        if isinstance(event, MarketReconciliationRequested):
            return (f"{market_label} sync started", "Account sync started.")
        if isinstance(event, MarketReconciliationCompleted):
            return (f"{market_label} sync complete", "Account sync finished.")
        return (f"{market_label} activity", "Recent activity recorded.")

    def _runtime_state_change(self, previous_state: str | None, new_state: str | None) -> str | None:
        if previous_state is None or new_state is None:
            return None
        return f"{self._label(previous_state)} to {self._label(new_state)}"

    def _format_reason(self, reason: str | None) -> str | None:
        if reason is None:
            return None
        return reason[:1].upper() + reason[1:]

    def _label(self, value: str) -> str:
        return value.replace("_", " ").replace("-", " ").title()