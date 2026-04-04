"""Strategy plugin and risk evaluation domain models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from hashlib import sha1
from typing import Protocol

from omnibot_v3.domain.broker import NormalizedAccount, NormalizedPosition, OrderRequest
from omnibot_v3.domain.runtime import Market


def utc_now() -> datetime:
    return datetime.now(UTC)


class MarketRegime(StrEnum):
    UNKNOWN = "unknown"
    TRENDING = "trending"
    RANGE_BOUND = "range_bound"
    BREAKOUT = "breakout"
    HIGH_VOLATILITY = "high_volatility"
    RISK_OFF = "risk_off"


class SetupFamily(StrEnum):
    MOMENTUM = "momentum"
    TREND_PULLBACK = "trend_pullback"
    BREAKOUT_CONTINUATION = "breakout_continuation"
    MEAN_REVERSION = "mean_reversion"
    VOLATILITY_COMPRESSION = "volatility_compression"
    DEFENSIVE_EXIT = "defensive_exit"


@dataclass(frozen=True, slots=True)
class SignalEvidence:
    label: str
    value: str
    weight: Decimal | None = None
    note: str = ""


@dataclass(frozen=True, slots=True)
class MarketRegimeAssessment:
    regime: MarketRegime = MarketRegime.UNKNOWN
    confidence: Decimal = Decimal("0")
    rationale: str = ""
    supporting_factors: tuple[str, ...] = ()
    evaluated_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True, slots=True)
class TradeSetup:
    setup_id: str
    family: SetupFamily
    symbol: str
    direction: str
    confidence: Decimal
    rationale: str
    supporting_factors: tuple[str, ...] = ()
    time_horizon_minutes: int | None = None


@dataclass(frozen=True, slots=True)
class ExitCondition:
    reason: str
    trigger_price: Decimal | None = None
    invalid_after: datetime | None = None
    requires_profit: bool = False


@dataclass(frozen=True, slots=True)
class ExitPlan:
    hard_stop_price: Decimal | None = None
    profit_target_price: Decimal | None = None
    trailing_stop_ratio: Decimal | None = None
    scale_out_ratio: Decimal | None = None
    scale_out_ratios: tuple[Decimal, ...] = ()
    scale_out_trigger_ratios: tuple[Decimal, ...] = ()
    max_hold_minutes: int | None = None
    rationale: str = ""
    conditions: tuple[ExitCondition, ...] = ()


@dataclass(frozen=True, slots=True)
class DecisionExplanation:
    summary: str
    reasons: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    evidence: tuple[SignalEvidence, ...] = ()


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
    position_opened_at: datetime | None = None
    evaluated_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True, slots=True)
class StrategySignal:
    strategy_id: str
    order_request: OrderRequest
    rationale: str
    confidence: Decimal | None = None
    regime: MarketRegimeAssessment | None = None
    setup: TradeSetup | None = None
    exit_plan: ExitPlan | None = None
    explanation: DecisionExplanation | None = None
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
    confidence: Decimal | None = None
    regime: MarketRegimeAssessment | None = None
    setup: TradeSetup | None = None
    exit_plan: ExitPlan | None = None
    explanation: DecisionExplanation | None = None
    generated_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True, slots=True)
class StrategyCandidate:
    symbol: str
    strategy_id: str
    profile_id: str
    order_request: OrderRequest
    score: Decimal
    confidence: Decimal | None = None
    regime: MarketRegimeAssessment | None = None
    setup: TradeSetup | None = None
    exit_plan: ExitPlan | None = None
    explanation: DecisionExplanation | None = None
    rationale: str = ""
    evidence: tuple[str, ...] = ()
    created_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True, slots=True)
class CandidateSelection:
    symbol: str
    selected: StrategyCandidate
    considered: tuple[StrategyCandidate, ...] = ()
    selected_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True, slots=True)
class TradeThesis:
    thesis_id: str
    symbol: str
    strategy_id: str
    profile_id: str
    order_request: OrderRequest
    score: Decimal
    confidence: Decimal | None = None
    regime: MarketRegimeAssessment | None = None
    setup: TradeSetup | None = None
    exit_plan: ExitPlan | None = None
    explanation: DecisionExplanation | None = None
    rationale: str = ""
    evidence: tuple[str, ...] = ()
    created_at: datetime = field(default_factory=utc_now)


def build_trade_thesis(candidate: StrategyCandidate) -> TradeThesis:
    setup_family = candidate.setup.family.value if candidate.setup is not None else "na"
    regime = candidate.regime.regime.value if candidate.regime is not None else "na"
    raw_identity = "|".join(
        (
            candidate.symbol.upper(),
            candidate.strategy_id,
            candidate.profile_id,
            candidate.order_request.side.value,
            setup_family,
            regime,
        )
    )
    thesis_id = f"thesis-{sha1(raw_identity.encode('utf-8')).hexdigest()[:12]}"
    return TradeThesis(
        thesis_id=thesis_id,
        symbol=candidate.symbol,
        strategy_id=candidate.strategy_id,
        profile_id=candidate.profile_id,
        order_request=candidate.order_request,
        score=candidate.score,
        confidence=candidate.confidence,
        regime=candidate.regime,
        setup=candidate.setup,
        exit_plan=candidate.exit_plan,
        explanation=candidate.explanation,
        rationale=candidate.rationale,
        evidence=candidate.evidence,
        created_at=candidate.created_at,
    )


class StrategyPlugin(Protocol):
    @property
    def profile(self) -> StrategyProfile:
        """Return strategy metadata used by the runtime and audit pipeline."""

    def generate_signal(self, context: StrategyContext) -> StrategySignal | None:
        """Return the next strategy signal or None when no action should be taken."""