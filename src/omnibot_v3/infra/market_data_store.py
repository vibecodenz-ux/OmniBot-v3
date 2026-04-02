"""In-memory and JSON-backed persistence adapters for normalized historical bars."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import Lock
from typing import ClassVar

from omnibot_v3.domain.broker import BarTimeframe, HistoricalBar
from omnibot_v3.domain.runtime import Market
from omnibot_v3.services.market_data_store import HistoricalBarStore


def _bar_key(bar: HistoricalBar) -> tuple[str, str, str, str]:
    return (bar.market.value, bar.symbol.upper(), bar.timeframe.value, bar.opened_at.isoformat())


@dataclass(slots=True)
class InMemoryHistoricalBarStore(HistoricalBarStore):
    _bars: dict[tuple[str, str, str, str], HistoricalBar] = field(default_factory=dict)

    def load(
        self,
        market: Market,
        symbol: str,
        timeframe: BarTimeframe,
        *,
        limit: int | None = None,
    ) -> tuple[HistoricalBar, ...]:
        bars = [
            bar
            for bar in self._bars.values()
            if bar.market == market and bar.symbol.upper() == symbol.upper() and bar.timeframe == timeframe
        ]
        bars.sort(key=lambda item: item.opened_at)
        if limit is not None:
            bars = bars[-max(limit, 0) :]
        return tuple(bars)

    def save(self, bars: list[HistoricalBar] | tuple[HistoricalBar, ...]) -> None:
        for bar in bars:
            self._bars[_bar_key(bar)] = bar


@dataclass(frozen=True, slots=True)
class JsonFileHistoricalBarStore(HistoricalBarStore):
    path: Path
    _path_locks: ClassVar[dict[str, Lock]] = {}
    _path_locks_guard: ClassVar[Lock] = Lock()

    def load(
        self,
        market: Market,
        symbol: str,
        timeframe: BarTimeframe,
        *,
        limit: int | None = None,
    ) -> tuple[HistoricalBar, ...]:
        with self._lock_for_path():
            bars = [
                bar
                for bar in self._load_all().values()
                if bar.market == market and bar.symbol.upper() == symbol.upper() and bar.timeframe == timeframe
            ]
        bars.sort(key=lambda item: item.opened_at)
        if limit is not None:
            bars = bars[-max(limit, 0) :]
        return tuple(bars)

    def save(self, bars: list[HistoricalBar] | tuple[HistoricalBar, ...]) -> None:
        with self._lock_for_path():
            merged = self._load_all()
            for bar in bars:
                merged[_bar_key(bar)] = bar
            self._write(merged.values())

    def _lock_for_path(self) -> Lock:
        normalized_path = str(self.path.resolve())
        with self._path_locks_guard:
            lock = self._path_locks.get(normalized_path)
            if lock is None:
                lock = Lock()
                self._path_locks[normalized_path] = lock
            return lock

    def _load_all(self) -> dict[tuple[str, str, str, str], HistoricalBar]:
        if not self.path.is_file():
            return {}

        payload = json.loads(self.path.read_text(encoding="utf-8"))
        raw_bars = payload.get("bars", [])
        if not isinstance(raw_bars, list):
            raise ValueError("Historical bar file has an invalid bars payload.")
        bars = [_bar_from_payload(item) for item in raw_bars]
        return {_bar_key(bar): bar for bar in bars}

    def _write(self, bars: Iterable[HistoricalBar]) -> None:
        normalized_bars = sorted(
            list(bars),
            key=lambda item: (item.market.value, item.symbol.upper(), item.timeframe.value, item.opened_at),
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"bars": [_bar_to_payload(bar) for bar in normalized_bars]}
        with NamedTemporaryFile("w", encoding="utf-8", dir=self.path.parent, delete=False) as handle:
            temp_path = Path(handle.name)
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        self._replace_with_retry(temp_path)

    def _replace_with_retry(self, temp_path: Path) -> None:
        last_error: PermissionError | None = None
        for attempt in range(5):
            try:
                os.replace(temp_path, self.path)
                return
            except PermissionError as exc:
                last_error = exc
                time.sleep(0.05 * (attempt + 1))
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)
        if last_error is not None:
            raise last_error


def _bar_from_payload(payload: object) -> HistoricalBar:
    if not isinstance(payload, dict):
        raise ValueError("Historical bar entry must be an object.")
    return HistoricalBar(
        market=Market(str(payload["market"])),
        symbol=str(payload["symbol"]),
        timeframe=BarTimeframe(str(payload["timeframe"])),
        open_price=_decimal_string(payload.get("open_price")),
        high_price=_decimal_string(payload.get("high_price")),
        low_price=_decimal_string(payload.get("low_price")),
        close_price=_decimal_string(payload.get("close_price")),
        volume=_decimal_string(payload.get("volume") or "0"),
        opened_at=_parse_datetime(payload.get("opened_at")),
        closed_at=_parse_datetime(payload.get("closed_at")),
    )


def _bar_to_payload(bar: HistoricalBar) -> dict[str, object]:
    return {
        "market": bar.market.value,
        "symbol": bar.symbol,
        "timeframe": bar.timeframe.value,
        "open_price": str(bar.open_price),
        "high_price": str(bar.high_price),
        "low_price": str(bar.low_price),
        "close_price": str(bar.close_price),
        "volume": str(bar.volume),
        "opened_at": bar.opened_at.isoformat(),
        "closed_at": bar.closed_at.isoformat(),
    }


def _parse_datetime(value: object) -> datetime:
    if value is None:
        raise ValueError("Historical bar timestamp is required.")
    return datetime.fromisoformat(str(value))


def _decimal_string(value: object):
    from decimal import Decimal

    return Decimal(str(value))