"""Persistence ports for normalized historical market bars."""

from __future__ import annotations

from typing import Protocol

from omnibot_v3.domain.broker import BarTimeframe, HistoricalBar
from omnibot_v3.domain.runtime import Market


class HistoricalBarStore(Protocol):
    def load(
        self,
        market: Market,
        symbol: str,
        timeframe: BarTimeframe,
        *,
        limit: int | None = None,
    ) -> tuple[HistoricalBar, ...]:
        """Load persisted bars for a market, symbol, and timeframe ordered oldest to newest."""

    def save(self, bars: list[HistoricalBar] | tuple[HistoricalBar, ...]) -> None:
        """Persist one or more bars, replacing duplicates by market, symbol, timeframe, and open time."""