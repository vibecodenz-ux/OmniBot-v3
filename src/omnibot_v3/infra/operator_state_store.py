"""In-memory and JSON-backed operator state persistence adapters."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from omnibot_v3.domain.runtime import Market
from omnibot_v3.services.operator_state import (
    OperatorStateSnapshot,
    OperatorStateStore,
    StoredTradingModuleSelection,
)


@dataclass(slots=True)
class InMemoryOperatorStateStore(OperatorStateStore):
    snapshot: OperatorStateSnapshot

    def __init__(self, snapshot: OperatorStateSnapshot | None = None) -> None:
        self.snapshot = snapshot or OperatorStateSnapshot()

    def load(self) -> OperatorStateSnapshot:
        return self.snapshot

    def save(self, snapshot: OperatorStateSnapshot) -> None:
        self.snapshot = snapshot


@dataclass(frozen=True, slots=True)
class JsonFileOperatorStateStore(OperatorStateStore):
    path: Path

    def load(self) -> OperatorStateSnapshot:
        if not self.path.is_file():
            return OperatorStateSnapshot()

        payload = json.loads(self.path.read_text(encoding="utf-8"))
        raw_selections = payload.get("trading_module_selections", {})
        if not isinstance(raw_selections, dict):
            raise ValueError("Operator state file has an invalid trading_module_selections payload.")

        selections: dict[Market, StoredTradingModuleSelection] = {}
        for raw_market, raw_selection in raw_selections.items():
            if not isinstance(raw_selection, dict):
                raise ValueError("Operator state file has an invalid trading module selection entry.")
            selections[Market(raw_market)] = StoredTradingModuleSelection(
                strategy_id=str(raw_selection["strategy_id"]),
                profile_id=str(raw_selection["profile_id"]),
                updated_at=_parse_datetime(raw_selection.get("updated_at")),
            )

        return OperatorStateSnapshot(
            admin_password_hash=_optional_string(payload.get("admin_password_hash")),
            trading_module_selections=selections,
            closed_trade_history_cleared_before=_parse_optional_datetime(payload.get("closed_trade_history_cleared_before")),
            updated_at=_parse_datetime(payload.get("updated_at")),
        )

    def save(self, snapshot: OperatorStateSnapshot) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "admin_password_hash": snapshot.admin_password_hash,
            "closed_trade_history_cleared_before": (
                snapshot.closed_trade_history_cleared_before.isoformat()
                if snapshot.closed_trade_history_cleared_before is not None
                else None
            ),
            "updated_at": snapshot.updated_at.isoformat(),
            "trading_module_selections": {
                market.value: {
                    "strategy_id": selection.strategy_id,
                    "profile_id": selection.profile_id,
                    "updated_at": selection.updated_at.isoformat(),
                }
                for market, selection in snapshot.trading_module_selections.items()
            },
        }
        self.path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _parse_datetime(value: object) -> datetime:
    if value is None:
        return datetime.now(UTC)
    return datetime.fromisoformat(str(value))


def _parse_optional_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(str(value))