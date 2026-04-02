"""In-memory session store for development and tests."""

from __future__ import annotations

from dataclasses import dataclass, field

from omnibot_v3.domain.auth import AuthenticatedSession
from omnibot_v3.services.session_auth import SessionStore


@dataclass(slots=True)
class InMemorySessionStore(SessionStore):
    sessions: dict[str, AuthenticatedSession] = field(default_factory=dict)

    def save(self, session: AuthenticatedSession) -> None:
        self.sessions[session.session_id] = session

    def get(self, session_id: str) -> AuthenticatedSession | None:
        return self.sessions.get(session_id)

    def delete(self, session_id: str) -> None:
        self.sessions.pop(session_id, None)

    def delete_all(self) -> None:
        self.sessions.clear()