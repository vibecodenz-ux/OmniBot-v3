"""Layered trading decision engine scaffolding."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from omnibot_v3.domain.broker import OrderRequest
from omnibot_v3.domain.strategy import (
    DecisionExplanation,
    ExitPlan,
    MarketRegimeAssessment,
    StrategyContext,
    StrategyProfile,
    StrategySignal,
    TradeSetup,
)


def _unique_reasons(*values: str) -> tuple[str, ...]:
    seen: set[str] = set()
    reasons: list[str] = []
    for value in values:
        normalized = value.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        reasons.append(normalized)
    return tuple(reasons)


class RegimeClassifier(Protocol):
    def assess(self, context: StrategyContext) -> MarketRegimeAssessment:
        """Classify the current market state for downstream decision stages."""


class SetupPlanner(Protocol):
    def detect_setup(
        self,
        context: StrategyContext,
        regime: MarketRegimeAssessment,
    ) -> TradeSetup | None:
        """Return the best actionable setup or None when no trade should be opened."""


class ExecutionPlanner(Protocol):
    def build_order_request(
        self,
        context: StrategyContext,
        regime: MarketRegimeAssessment,
        setup: TradeSetup,
    ) -> OrderRequest | None:
        """Translate an approved setup into a broker-normalized order request."""


class ExitPlanner(Protocol):
    def build_exit_plan(
        self,
        context: StrategyContext,
        regime: MarketRegimeAssessment,
        setup: TradeSetup,
        order_request: OrderRequest,
    ) -> ExitPlan | None:
        """Build the initial exit plan for a new position."""


class ExplanationBuilder(Protocol):
    def build_explanation(
        self,
        context: StrategyContext,
        regime: MarketRegimeAssessment,
        setup: TradeSetup,
        order_request: OrderRequest,
        exit_plan: ExitPlan | None,
    ) -> DecisionExplanation:
        """Build the operator-facing explanation for the generated signal."""


@dataclass(frozen=True, slots=True)
class LayeredStrategyPlugin:
    profile: StrategyProfile
    regime_classifier: RegimeClassifier
    setup_planner: SetupPlanner
    execution_planner: ExecutionPlanner
    exit_planner: ExitPlanner | None = None
    explanation_builder: ExplanationBuilder | None = None

    def generate_signal(self, context: StrategyContext) -> StrategySignal | None:
        regime = self.regime_classifier.assess(context)
        setup = self.setup_planner.detect_setup(context, regime)
        if setup is None:
            return None

        order_request = self.execution_planner.build_order_request(context, regime, setup)
        if order_request is None:
            return None

        exit_plan = None
        if self.exit_planner is not None:
            exit_plan = self.exit_planner.build_exit_plan(context, regime, setup, order_request)

        explanation = self._build_explanation(context, regime, setup, order_request, exit_plan)

        return StrategySignal(
            strategy_id=self.profile.strategy_id,
            order_request=order_request,
            rationale=setup.rationale,
            confidence=setup.confidence,
            regime=regime,
            setup=setup,
            exit_plan=exit_plan,
            explanation=explanation,
        )

    def _build_explanation(
        self,
        context: StrategyContext,
        regime: MarketRegimeAssessment,
        setup: TradeSetup,
        order_request: OrderRequest,
        exit_plan: ExitPlan | None,
    ) -> DecisionExplanation:
        if self.explanation_builder is not None:
            return self.explanation_builder.build_explanation(
                context=context,
                regime=regime,
                setup=setup,
                order_request=order_request,
                exit_plan=exit_plan,
            )

        summary = setup.rationale or regime.rationale or "Signal generated"
        exit_reason = ""
        if exit_plan is not None:
            exit_reason = exit_plan.rationale
        return DecisionExplanation(
            summary=summary,
            reasons=_unique_reasons(regime.rationale, setup.rationale, exit_reason),
        )