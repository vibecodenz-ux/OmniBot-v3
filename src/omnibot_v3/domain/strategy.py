"""Strategy plugin and risk evaluation domain models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Protocol

from omnibot_v3.domain.broker import NormalizedAccount, NormalizedPosition, OrderRequest
from omnibot_v3.domain.runtime import Market


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class StrategyProfile:
    strategy_id: str
    name: str
    version: str
    market: Market
    description: str = ""
    tags: tuple[str, ...] = ()
    parameters: tuple[tuple[str, str], ...] = ()
    enabled: bool = True


@dataclass(frozen=True, slots=True)
class StrategyContext:
    market: Market
    account: NormalizedAccount
    positions: tuple[NormalizedPosition, ...]
    latest_price: Decimal | None = None
    bar_timestamp: datetime | None = None
    evaluated_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True, slots=True)
class StrategySignal:
    strategy_id: str
    order_request: OrderRequest
    rationale: str
    created_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True, slots=True)
class RiskPolicy:
    max_order_notional: Decimal
    max_position_notional: Decimal
    max_daily_loss: Decimal
    max_drawdown: Decimal


@dataclass(frozen=True, slots=True)
class RiskPolicyOverride:
    market: Market
    max_order_notional: Decimal | None = None
    max_position_notional: Decimal | None = None
    max_daily_loss: Decimal | None = None
    max_drawdown: Decimal | None = None


@dataclass(frozen=True, slots=True)
class PreTradeDecision:
    accepted: bool
    reason: str
    checked_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True, slots=True)
class StrategyExecutionResult:
    strategy_id: str
    strategy_version: str
    decision: PreTradeDecision
    order_request: OrderRequest | None = None
    profile_tags: tuple[str, ...] = ()
    generated_at: datetime = field(default_factory=utc_now)


class StrategyPlugin(Protocol):
    @property
    def profile(self) -> StrategyProfile:
        """Return strategy metadata used by the runtime and audit pipeline."""

    def generate_signal(self, context: StrategyContext) -> StrategySignal | None:
        """Return the next strategy signal or None when no action should be taken."""