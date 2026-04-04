"""Risk policy engine and strategy runtime helpers."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from omnibot_v3.domain.broker import NormalizedAccount, NormalizedPosition, OrderRequest, OrderSide
from omnibot_v3.domain.strategy import (
    PreTradeDecision,
    RiskPolicy,
    RiskPolicyOverride,
    StrategyContext,
    StrategyExecutionResult,
    StrategyPlugin,
)


def _position_notional(position: NormalizedPosition) -> Decimal:
    return position.quantity * position.market_price


def _order_notional(order_request: OrderRequest) -> Decimal:
    reference_price = order_request.limit_price if order_request.limit_price is not None else Decimal("0")
    return order_request.quantity * reference_price


@dataclass(frozen=True, slots=True)
class RiskPolicyEngine:
    policy: RiskPolicy
    overrides: tuple[RiskPolicyOverride, ...] = ()

    def resolve_policy(self, market) -> RiskPolicy:
        override = next((candidate for candidate in self.overrides if candidate.market == market), None)
        if override is None:
            return self.policy

        return RiskPolicy(
            max_order_notional=override.max_order_notional or self.policy.max_order_notional,
            max_position_notional=override.max_position_notional or self.policy.max_position_notional,
            max_daily_loss=override.max_daily_loss or self.policy.max_daily_loss,
            max_drawdown=override.max_drawdown or self.policy.max_drawdown,
        )

    def evaluate_order(
        self,
        market,
        account: NormalizedAccount,
        positions: tuple[NormalizedPosition, ...],
        order_request: OrderRequest,
        cumulative_daily_pnl: Decimal = Decimal("0"),
        drawdown: Decimal = Decimal("0"),
    ) -> PreTradeDecision:
        policy = self.resolve_policy(market)
        order_notional = _order_notional(order_request)
        if order_notional > policy.max_order_notional:
            return PreTradeDecision(accepted=False, reason="order notional exceeds risk policy")

        current_position_notional = sum((_position_notional(position) for position in positions), Decimal("0"))
        projected_notional = current_position_notional + order_notional
        if order_request.side == OrderSide.SELL:
            projected_notional = max(Decimal("0"), current_position_notional - order_notional)
        if projected_notional > policy.max_position_notional:
            return PreTradeDecision(accepted=False, reason="position notional exceeds risk policy")

        if cumulative_daily_pnl <= -policy.max_daily_loss:
            return PreTradeDecision(accepted=False, reason="daily loss limit reached")

        if drawdown >= policy.max_drawdown:
            return PreTradeDecision(accepted=False, reason="drawdown limit reached")

        if order_notional > account.buying_power:
            return PreTradeDecision(accepted=False, reason="order exceeds buying power")

        return PreTradeDecision(accepted=True, reason="accepted")


@dataclass(frozen=True, slots=True)
class StrategyRuntime:
    plugin: StrategyPlugin
    risk_engine: RiskPolicyEngine

    def evaluate(
        self,
        context: StrategyContext,
        cumulative_daily_pnl: Decimal = Decimal("0"),
        drawdown: Decimal = Decimal("0"),
    ) -> StrategyExecutionResult:
        signal = self.plugin.generate_signal(context)
        if signal is None:
            return StrategyExecutionResult(
                strategy_id=self.plugin.profile.strategy_id,
                strategy_version=self.plugin.profile.version,
                decision=PreTradeDecision(accepted=False, reason="no signal generated"),
                profile_tags=self.plugin.profile.tags,
            )

        decision = self.risk_engine.evaluate_order(
            market=context.market,
            account=context.account,
            positions=context.positions,
            order_request=signal.order_request,
            cumulative_daily_pnl=cumulative_daily_pnl,
            drawdown=drawdown,
        )
        if not decision.accepted:
            return StrategyExecutionResult(
                strategy_id=self.plugin.profile.strategy_id,
                strategy_version=self.plugin.profile.version,
                decision=decision,
                profile_tags=self.plugin.profile.tags,
                confidence=signal.confidence,
                regime=signal.regime,
                setup=signal.setup,
                exit_plan=signal.exit_plan,
                explanation=signal.explanation,
            )

        return StrategyExecutionResult(
            strategy_id=self.plugin.profile.strategy_id,
            strategy_version=self.plugin.profile.version,
            decision=decision,
            order_request=signal.order_request,
            profile_tags=self.plugin.profile.tags,
            confidence=signal.confidence,
            regime=signal.regime,
            setup=signal.setup,
            exit_plan=signal.exit_plan,
            explanation=signal.explanation,
        )