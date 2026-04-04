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
        raw_active_trade_theses = payload.get("active_trade_theses", {})
        raw_closed_trade_theses = payload.get("closed_trade_theses", {})
        if not isinstance(raw_selections, dict):
            raise ValueError("Operator state file has an invalid trading_module_selections payload.")
        if not isinstance(raw_active_trade_theses, dict):
            raise ValueError("Operator state file has an invalid active_trade_theses payload.")
        if not isinstance(raw_closed_trade_theses, dict):
            raise ValueError("Operator state file has an invalid closed_trade_theses payload.")

        selections: dict[Market, StoredTradingModuleSelection] = {}
        for raw_market, raw_selection in raw_selections.items():
            if not isinstance(raw_selection, dict):
                raise ValueError("Operator state file has an invalid trading module selection entry.")
            selections[Market(raw_market)] = StoredTradingModuleSelection(
                profile_id=str(raw_selection["profile_id"]),
                updated_at=_parse_datetime(raw_selection.get("updated_at")),
                strategy_id=_optional_string(raw_selection.get("strategy_id")),
            )

        active_trade_theses = _parse_thesis_map(raw_active_trade_theses, "active trade thesis")
        closed_trade_theses = _parse_thesis_map(raw_closed_trade_theses, "closed trade thesis")

        return OperatorStateSnapshot(
            admin_password_hash=_optional_string(payload.get("admin_password_hash")),
            trading_module_selections=selections,
            active_trade_theses=active_trade_theses,
            closed_trade_theses=closed_trade_theses,
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
            "active_trade_theses": {
                market.value: {
                    symbol: dict(thesis)
                    for symbol, thesis in stored_theses.items()
                    if isinstance(thesis, dict)
                }
                for market, stored_theses in snapshot.active_trade_theses.items()
            },
            "closed_trade_theses": {
                market.value: {
                    trade_id: dict(thesis)
                    for trade_id, thesis in stored_theses.items()
                    if isinstance(thesis, dict)
                }
                for market, stored_theses in snapshot.closed_trade_theses.items()
            },
            "updated_at": snapshot.updated_at.isoformat(),
            "trading_module_selections": {
                market.value: {
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


def _parse_thesis_map(
    raw_value: object,
    entry_name: str,
) -> dict[Market, dict[str, dict[str, object]]]:
    if not isinstance(raw_value, dict):
        raise ValueError(f"Operator state file has an invalid {entry_name} payload.")

    parsed: dict[Market, dict[str, dict[str, object]]] = {}
    for raw_market, raw_entries in raw_value.items():
        if not isinstance(raw_entries, dict):
            raise ValueError(f"Operator state file has an invalid {entry_name} entry.")
        parsed[Market(raw_market)] = {
            str(entry_key): dict(entry_value)
            for entry_key, entry_value in raw_entries.items()
            if isinstance(entry_value, dict)
        }
    return parsed