"""Reusable rolling decision helpers shared by the live scanner migration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Protocol, TypedDict

from omnibot_v3.domain import (
    DecisionExplanation,
    ExitCondition,
    ExitPlan,
    HistoricalBar,
    Market,
    MarketRegime,
    MarketRegimeAssessment,
    NormalizedPosition,
    OrderRequest,
    OrderSide,
    OrderType,
    SetupFamily,
    SignalEvidence,
    StrategyContext,
    StrategyExecutionResult,
    StrategyProfile,
    TradeSetup,
)
from omnibot_v3.services.decision_engine import LayeredStrategyPlugin


@dataclass(frozen=True, slots=True)
class RollingSignalDiagnostics:
    current_price: Decimal
    previous_price: Decimal
    baseline_price: Decimal
    delta_ratio: Decimal
    baseline_ratio: Decimal
    prior_high: Decimal
    prior_low: Decimal
    momentum_up: bool
    momentum_down: bool
    breakout_up: bool
    breakout_down: bool
    reversion_up: bool
    reversion_down: bool
    long_votes: int
    short_votes: int
    momentum_up_delta_min: Decimal
    momentum_up_baseline_min: Decimal
    momentum_down_delta_max: Decimal
    momentum_down_baseline_max: Decimal
    breakout_above_level: Decimal
    breakout_below_level: Decimal
    reversion_up_baseline_max: Decimal
    reversion_up_delta_min: Decimal
    reversion_down_baseline_min: Decimal
    reversion_down_delta_max: Decimal


class ProfileSettings(TypedDict):
    target_notional: Decimal
    threshold_bias: Decimal
    breakout_buffer: Decimal
    cooldown_seconds: int
    stop_loss_ratio: Decimal
    min_net_profit_ratio: Decimal
    max_hold_minutes: int


class RollingDecisionSupport(Protocol):
    def _regime_assessment(self, diagnostics: RollingSignalDiagnostics) -> MarketRegimeAssessment: ...

    def _detect_setup(
        self,
        context: StrategyContext,
        diagnostics: RollingSignalDiagnostics,
        position: NormalizedPosition | None,
    ) -> TradeSetup | None: ...

    def _build_order_request(
        self,
        setup: TradeSetup,
        diagnostics: RollingSignalDiagnostics,
        position: NormalizedPosition | None,
    ) -> OrderRequest: ...

    def _exit_plan(
        self,
        context: StrategyContext,
        order_request: OrderRequest,
        rationale: str,
        position: NormalizedPosition | None,
    ) -> ExitPlan: ...

    def _decision_explanation(
        self,
        regime: MarketRegimeAssessment,
        setup: TradeSetup,
        order_request: OrderRequest,
        exit_plan: ExitPlan,
        diagnostics: RollingSignalDiagnostics,
        position: NormalizedPosition | None,
    ) -> DecisionExplanation: ...


def build_rolling_layered_plugin(
    profile: StrategyProfile,
    plugin: RollingDecisionSupport,
    diagnostics: RollingSignalDiagnostics,
    position: NormalizedPosition | None,
) -> LayeredStrategyPlugin:
    return LayeredStrategyPlugin(
        profile=profile,
        regime_classifier=RollingRegimeClassifier(plugin=plugin, diagnostics=diagnostics),
        setup_planner=RollingSetupPlanner(plugin=plugin, diagnostics=diagnostics, position=position),
        execution_planner=RollingExecutionPlanner(plugin=plugin, diagnostics=diagnostics, position=position),
        exit_planner=RollingExitPlanner(plugin=plugin, diagnostics=diagnostics, position=position),
        explanation_builder=RollingExplanationBuilder(plugin=plugin, diagnostics=diagnostics, position=position),
    )


@dataclass(frozen=True, slots=True)
class RollingRegimeClassifier:
    plugin: RollingDecisionSupport
    diagnostics: RollingSignalDiagnostics

    def assess(self, context: StrategyContext) -> MarketRegimeAssessment:
        del context
        return self.plugin._regime_assessment(self.diagnostics)


@dataclass(frozen=True, slots=True)
class RollingSetupPlanner:
    plugin: RollingDecisionSupport
    diagnostics: RollingSignalDiagnostics
    position: NormalizedPosition | None

    def detect_setup(
        self,
        context: StrategyContext,
        regime: MarketRegimeAssessment,
    ) -> TradeSetup | None:
        del regime
        return self.plugin._detect_setup(context, self.diagnostics, self.position)


@dataclass(frozen=True, slots=True)
class RollingExecutionPlanner:
    plugin: RollingDecisionSupport
    diagnostics: RollingSignalDiagnostics
    position: NormalizedPosition | None

    def build_order_request(
        self,
        context: StrategyContext,
        regime: MarketRegimeAssessment,
        setup: TradeSetup,
    ) -> OrderRequest | None:
        del context, regime
        return self.plugin._build_order_request(setup, self.diagnostics, self.position)


@dataclass(frozen=True, slots=True)
class RollingExitPlanner:
    plugin: RollingDecisionSupport
    diagnostics: RollingSignalDiagnostics
    position: NormalizedPosition | None

    def build_exit_plan(
        self,
        context: StrategyContext,
        regime: MarketRegimeAssessment,
        setup: TradeSetup,
        order_request: OrderRequest,
    ) -> ExitPlan | None:
        del regime
        return self.plugin._exit_plan(context, order_request, setup.rationale, self.position)


@dataclass(frozen=True, slots=True)
class RollingExplanationBuilder:
    plugin: RollingDecisionSupport
    diagnostics: RollingSignalDiagnostics
    position: NormalizedPosition | None

    def build_explanation(
        self,
        context: StrategyContext,
        regime: MarketRegimeAssessment,
        setup: TradeSetup,
        order_request: OrderRequest,
        exit_plan: ExitPlan | None,
    ) -> DecisionExplanation:
        del context
        assert exit_plan is not None
        return self.plugin._decision_explanation(regime, setup, order_request, exit_plan, self.diagnostics, self.position)


def result_detail_lines(result: StrategyExecutionResult) -> tuple[str, ...]:
    details: list[str] = []
    if result.regime is not None:
        details.append(
            f"regime={result.regime.regime.value} confidence={result.regime.confidence:.2f} rationale={result.regime.rationale}"
        )
    if result.setup is not None:
        details.append(
            f"setup={result.setup.family.value} direction={result.setup.direction} confidence={result.setup.confidence:.2f}"
        )
    if result.exit_plan is not None:
        parts = [f"exit_rationale={result.exit_plan.rationale}"]
        if result.exit_plan.hard_stop_price is not None:
            parts.append(f"stop={result.exit_plan.hard_stop_price:.6f}")
        if result.exit_plan.profit_target_price is not None:
            parts.append(f"target={result.exit_plan.profit_target_price:.6f}")
        if result.exit_plan.trailing_stop_ratio is not None:
            parts.append(f"trail={result.exit_plan.trailing_stop_ratio:.4f}")
        if result.exit_plan.max_hold_minutes is not None:
            parts.append(f"max_hold={result.exit_plan.max_hold_minutes}m")
        details.append(" ".join(parts))
    if result.explanation is not None:
        details.append(f"summary={result.explanation.summary}")
        details.extend(f"reason={reason}" for reason in result.explanation.reasons if reason)
        details.extend(f"warning={warning}" for warning in result.explanation.warnings if warning)
        details.extend(
            f"evidence {item.label}={item.value}" for item in result.explanation.evidence if item.value
        )
    return tuple(details)


@dataclass(frozen=True, slots=True)
class RollingLegacyDecisionSupport:
    profile: StrategyProfile
    profile_id: str
    symbol: str
    recent_prices: tuple[Decimal, ...]
    quantity: Decimal
    recent_bars: tuple[HistoricalBar, ...] = ()
    allow_short: bool = False

    def signal_diagnostics(self) -> RollingSignalDiagnostics | None:
        settings = profile_settings(self.profile_id)
        if len(self.recent_bars) >= 3:
            current_price = self.recent_bars[-1].close_price
            previous_price = self.recent_bars[-2].close_price
            baseline_price = sum((bar.close_price for bar in self.recent_bars[:-1]), Decimal("0")) / Decimal(len(self.recent_bars) - 1)
            prior_high = max(bar.high_price for bar in self.recent_bars[:-1])
            prior_low = min(bar.low_price for bar in self.recent_bars[:-1])
        else:
            current_price = self.recent_prices[-1]
            previous_price = self.recent_prices[-2]
            baseline_price = sum(self.recent_prices[:-1], Decimal("0")) / Decimal(len(self.recent_prices) - 1)
            prior_high = max(self.recent_prices[:-1])
            prior_low = min(self.recent_prices[:-1])
        if previous_price <= Decimal("0") or baseline_price <= Decimal("0"):
            return None
        delta_ratio = (current_price - previous_price) / previous_price
        baseline_ratio = (current_price - baseline_price) / baseline_price
        momentum_up_delta_min = Decimal("0.003") + settings["threshold_bias"]
        momentum_up_baseline_min = Decimal("0.004") + settings["threshold_bias"]
        momentum_down_delta_max = Decimal("-0.004") - settings["threshold_bias"]
        momentum_down_baseline_max = Decimal("-0.005") - settings["threshold_bias"]
        breakout_above_level = prior_high * (Decimal("1.002") - settings["breakout_buffer"])
        breakout_below_level = prior_low * (Decimal("0.998") + settings["breakout_buffer"])
        reversion_up_baseline_max = Decimal("-0.007") - settings["threshold_bias"]
        reversion_up_delta_min = Decimal("0.002") - settings["threshold_bias"]
        reversion_down_baseline_min = Decimal("0.007") + settings["threshold_bias"]
        reversion_down_delta_max = Decimal("-0.002") + settings["threshold_bias"]
        momentum_up = delta_ratio >= momentum_up_delta_min and baseline_ratio >= momentum_up_baseline_min
        momentum_down = delta_ratio <= momentum_down_delta_max and baseline_ratio <= momentum_down_baseline_max
        breakout_up = current_price >= breakout_above_level
        breakout_down = current_price <= breakout_below_level
        reversion_up = baseline_ratio <= reversion_up_baseline_max and delta_ratio >= reversion_up_delta_min
        reversion_down = baseline_ratio >= reversion_down_baseline_min and delta_ratio <= reversion_down_delta_max
        return RollingSignalDiagnostics(
            current_price=current_price,
            previous_price=previous_price,
            baseline_price=baseline_price,
            delta_ratio=delta_ratio,
            baseline_ratio=baseline_ratio,
            prior_high=prior_high,
            prior_low=prior_low,
            momentum_up=momentum_up,
            momentum_down=momentum_down,
            breakout_up=breakout_up,
            breakout_down=breakout_down,
            reversion_up=reversion_up,
            reversion_down=reversion_down,
            long_votes=sum((momentum_up, breakout_up, reversion_up)),
            short_votes=sum((momentum_down, breakout_down, reversion_down)),
            momentum_up_delta_min=momentum_up_delta_min,
            momentum_up_baseline_min=momentum_up_baseline_min,
            momentum_down_delta_max=momentum_down_delta_max,
            momentum_down_baseline_max=momentum_down_baseline_max,
            breakout_above_level=breakout_above_level,
            breakout_below_level=breakout_below_level,
            reversion_up_baseline_max=reversion_up_baseline_max,
            reversion_up_delta_min=reversion_up_delta_min,
            reversion_down_baseline_min=reversion_down_baseline_min,
            reversion_down_delta_max=reversion_down_delta_max,
        )

    def position_for(self, context: StrategyContext) -> NormalizedPosition | None:
        return next((item for item in context.positions if item.symbol.upper() == self.symbol.upper()), None)

    def explain_no_signal(self, context: StrategyContext) -> tuple[str, ...]:
        if len(self.recent_prices) < 3:
            return (f"history {len(self.recent_prices)}/3 bars collected before evaluation",)
        diagnostics = self.signal_diagnostics()
        if diagnostics is None:
            return ("price history is not usable yet",)

        position = self.position_for(context)
        if self.profile.strategy_id == "test_drive":
            if position is not None and position.quantity > 0:
                return (
                    f"test drive long exits when current<=previous; current={diagnostics.current_price:.6f}, previous={diagnostics.previous_price:.6f}",
                )
            if position is not None and position.quantity < 0:
                return (
                    f"test drive short exits when current>=previous; current={diagnostics.current_price:.6f}, previous={diagnostics.previous_price:.6f}",
                )
            if self.allow_short:
                return (
                    f"test drive enters BUY when current>=previous or SELL when current<previous; current={diagnostics.current_price:.6f}, previous={diagnostics.previous_price:.6f}",
                )
            return (
                f"test drive enters BUY once 3 bars exist; current={diagnostics.current_price:.6f}, previous={diagnostics.previous_price:.6f}",
            )

        if position is not None:
            settings = profile_settings(self.profile_id)
            estimated_cost = estimated_round_trip_cost_ratio(context.market)
            gross_pnl_ratio = _position_pnl_ratio(position, diagnostics.current_price)
            net_pnl_ratio = gross_pnl_ratio - estimated_cost
            hold_detail = (
                f"hold={int((context.evaluated_at - context.position_opened_at).total_seconds() // 60)}m vs max_hold={settings['max_hold_minutes']}m"
                if context.position_opened_at is not None
                else f"max_hold={settings['max_hold_minutes']}m once this runtime has tracked the trade"
            )
            baseline_gate = (
                f"legacy exit needs baseline_ratio <= -0.0080, current={diagnostics.baseline_ratio:.4f}"
                if position.quantity > 0
                else f"legacy exit needs baseline_ratio >= 0.0080, current={diagnostics.baseline_ratio:.4f}"
            )
            return (
                f"net_pnl_ratio={net_pnl_ratio:.4f} vs take_profit>={settings['min_net_profit_ratio']:.4f} after estimated cost {estimated_cost:.4f}",
                f"gross_pnl_ratio={gross_pnl_ratio:.4f} vs stop_loss<={-settings['stop_loss_ratio']:.4f}",
                hold_detail,
                baseline_gate,
            )

        if self.profile.strategy_id == "momentum":
            details = [
                f"delta_ratio={diagnostics.delta_ratio:.4f} vs buy>={diagnostics.momentum_up_delta_min:.4f}",
                f"baseline_ratio={diagnostics.baseline_ratio:.4f} vs buy>={diagnostics.momentum_up_baseline_min:.4f}",
            ]
            if self.allow_short:
                details.append(
                    f"short gate delta<={diagnostics.momentum_down_delta_max:.4f}, baseline<={diagnostics.momentum_down_baseline_max:.4f}"
                )
            return tuple(details)
        if self.profile.strategy_id == "breakout":
            details = [
                f"current={diagnostics.current_price:.6f} vs breakout_above={diagnostics.breakout_above_level:.6f}",
            ]
            if self.allow_short:
                details.append(
                    f"current={diagnostics.current_price:.6f} vs breakdown_below={diagnostics.breakout_below_level:.6f}"
                )
            return tuple(details)
        if self.profile.strategy_id == "mean_reversion":
            details = [
                f"baseline_ratio={diagnostics.baseline_ratio:.4f} vs buy<={diagnostics.reversion_up_baseline_max:.4f}",
                f"delta_ratio={diagnostics.delta_ratio:.4f} vs buy>={diagnostics.reversion_up_delta_min:.4f}",
            ]
            if self.allow_short:
                details.append(
                    f"short gate baseline>={diagnostics.reversion_down_baseline_min:.4f}, delta<={diagnostics.reversion_down_delta_max:.4f}"
                )
            return tuple(details)
        if self.profile.strategy_id == "ml_ensemble":
            return self._prepend_regime_detail(
                diagnostics,
                (
                    f"ensemble votes long={diagnostics.long_votes}/2 short={diagnostics.short_votes}/2",
                    f"momentum={_yes_no(diagnostics.momentum_up or diagnostics.momentum_down)} breakout={_yes_no(diagnostics.breakout_up or diagnostics.breakout_down)} reversion={_yes_no(diagnostics.reversion_up or diagnostics.reversion_down)}",
                ),
            )
        return self._prepend_regime_detail(
            diagnostics,
            (
                f"delta_ratio={diagnostics.delta_ratio:.4f}",
                f"baseline_ratio={diagnostics.baseline_ratio:.4f}",
            ),
        )

    def planned_exit_price(self, context: StrategyContext) -> Decimal | None:
        diagnostics = self.signal_diagnostics()
        if diagnostics is None:
            return None
        position = self.position_for(context)
        if position is None:
            return None

        if self.profile.strategy_id == "test_drive":
            return diagnostics.previous_price
        take_profit_ratio = estimated_round_trip_cost_ratio(context.market) + profile_settings(self.profile_id)["min_net_profit_ratio"]
        if position.quantity > 0:
            return position.average_price * (Decimal("1") + take_profit_ratio)
        if position.quantity < 0:
            return position.average_price * (Decimal("1") - take_profit_ratio)
        return None

    def _detect_setup(
        self,
        context: StrategyContext,
        diagnostics: RollingSignalDiagnostics,
        position: NormalizedPosition | None,
    ) -> TradeSetup | None:
        strategy_id = self.profile.strategy_id

        if strategy_id == "test_drive":
            if position is not None and position.quantity > 0 and diagnostics.current_price <= diagnostics.previous_price:
                return self._trade_setup(OrderSide.SELL, "test drive exit on any stall or downtick", diagnostics, position)
            if position is not None and position.quantity < 0 and diagnostics.current_price >= diagnostics.previous_price:
                return self._trade_setup(OrderSide.BUY, "test drive exit on any stall or uptick", diagnostics, position)
            if position is not None:
                return None
            if self.allow_short and diagnostics.current_price < diagnostics.previous_price:
                return self._trade_setup(OrderSide.SELL, "test drive short on minimal downside pressure", diagnostics, position)
            return self._trade_setup(OrderSide.BUY, "test drive long on minimal confirmation", diagnostics, position)

        if position is not None:
            managed_exit = self._managed_exit_setup(context, position, diagnostics)
            if managed_exit is not None:
                return managed_exit

        if position is not None:
            return None

        if strategy_id == "momentum":
            if diagnostics.momentum_up:
                return self._trade_setup(OrderSide.BUY, "momentum confirmation", diagnostics, position)
            if self.allow_short and diagnostics.momentum_down:
                return self._trade_setup(OrderSide.SELL, "downside momentum confirmation", diagnostics, position)

        if strategy_id == "breakout":
            if diagnostics.breakout_up:
                return self._trade_setup(OrderSide.BUY, "breakout above recent range", diagnostics, position)
            if self.allow_short and diagnostics.breakout_down:
                return self._trade_setup(OrderSide.SELL, "breakdown below recent range", diagnostics, position)

        if strategy_id == "mean_reversion":
            if diagnostics.reversion_up:
                return self._trade_setup(OrderSide.BUY, "reversion from downside stretch", diagnostics, position)
            if self.allow_short and diagnostics.reversion_down:
                return self._trade_setup(OrderSide.SELL, "reversion from upside stretch", diagnostics, position)

        if strategy_id == "ml_ensemble":
            if diagnostics.long_votes >= 2:
                return self._trade_setup(OrderSide.BUY, "ensemble confirmed long setup", diagnostics, position)
            if self.allow_short and diagnostics.short_votes >= 2:
                return self._trade_setup(OrderSide.SELL, "ensemble confirmed short setup", diagnostics, position)

        return None

    def _build_order_request(
        self,
        setup: TradeSetup,
        diagnostics: RollingSignalDiagnostics,
        position: NormalizedPosition | None,
    ) -> OrderRequest:
        side = OrderSide.SELL if setup.direction in {"short", "close-long"} else OrderSide.BUY
        quantity = abs(position.quantity) if position is not None else self.quantity
        return OrderRequest(
            client_order_id=_client_order_id(self.profile.market, self.symbol, self.profile.strategy_id),
            symbol=self.symbol,
            side=side,
            quantity=quantity,
            order_type=OrderType.MARKET,
            limit_price=diagnostics.current_price,
        )

    def _regime_assessment(self, diagnostics: RollingSignalDiagnostics) -> MarketRegimeAssessment:
        magnitude = max(abs(diagnostics.delta_ratio), abs(diagnostics.baseline_ratio))
        confidence = min(Decimal("0.95"), Decimal("0.20") + (magnitude * Decimal("40")))
        if diagnostics.breakout_up or diagnostics.breakout_down:
            return MarketRegimeAssessment(
                regime=MarketRegime.BREAKOUT,
                confidence=confidence,
                rationale="Price is pressing beyond the recent range boundary.",
                supporting_factors=(
                    f"current={diagnostics.current_price:.6f}",
                    f"range={diagnostics.prior_low:.6f}-{diagnostics.prior_high:.6f}",
                ),
            )
        if diagnostics.long_votes >= 2 or diagnostics.short_votes >= 2:
            return MarketRegimeAssessment(
                regime=MarketRegime.TRENDING,
                confidence=confidence,
                rationale="Multiple directional conditions agree on the current move.",
                supporting_factors=(
                    f"long_votes={diagnostics.long_votes}",
                    f"short_votes={diagnostics.short_votes}",
                ),
            )
        if magnitude >= Decimal("0.01"):
            return MarketRegimeAssessment(
                regime=MarketRegime.HIGH_VOLATILITY,
                confidence=confidence,
                rationale="Price is moving fast enough that execution quality matters more than frequency.",
                supporting_factors=(
                    f"delta_ratio={diagnostics.delta_ratio:.4f}",
                    f"baseline_ratio={diagnostics.baseline_ratio:.4f}",
                ),
            )
        if abs(diagnostics.delta_ratio) <= Decimal("0.0015") and abs(diagnostics.baseline_ratio) <= Decimal("0.0025"):
            return MarketRegimeAssessment(
                regime=MarketRegime.RANGE_BOUND,
                confidence=confidence,
                rationale="Price is staying close to its short baseline and recent range.",
                supporting_factors=(
                    f"delta_ratio={diagnostics.delta_ratio:.4f}",
                    f"baseline_ratio={diagnostics.baseline_ratio:.4f}",
                ),
            )
        return MarketRegimeAssessment(
            regime=MarketRegime.UNKNOWN,
            confidence=confidence,
            rationale="The current move is weak or mixed across the legacy signal checks.",
        )

    def _exit_plan(
        self,
        context: StrategyContext,
        order_request: OrderRequest,
        rationale: str,
        position: NormalizedPosition | None,
    ) -> ExitPlan:
        settings = profile_settings(self.profile_id)
        min_profit_ratio = settings["min_net_profit_ratio"] + estimated_round_trip_cost_ratio(context.market)
        stop_ratio = settings["stop_loss_ratio"]
        if position is not None:
            return ExitPlan(
                rationale=f"Exit signal emitted because {rationale}.",
                conditions=(ExitCondition(reason=rationale),),
            )
        reference_price = order_request.limit_price or context.latest_price or Decimal("0")
        if order_request.side == OrderSide.BUY:
            hard_stop_price = reference_price * (Decimal("1") - stop_ratio)
            profit_target_price = reference_price * (Decimal("1") + min_profit_ratio)
        else:
            hard_stop_price = reference_price * (Decimal("1") + stop_ratio)
            profit_target_price = reference_price * (Decimal("1") - min_profit_ratio)
        adaptive_scale_plan = adaptive_scale_management(
            strategy_id=self.profile.strategy_id,
            profile_id=self.profile_id,
            min_profit_ratio=min_profit_ratio,
        )
        return ExitPlan(
            hard_stop_price=hard_stop_price,
            profit_target_price=profit_target_price,
            trailing_stop_ratio=min_profit_ratio / Decimal("2"),
            scale_out_ratio=adaptive_scale_plan["scale_out_ratios"][0],
            scale_out_ratios=adaptive_scale_plan["scale_out_ratios"],
            scale_out_trigger_ratios=adaptive_scale_plan["scale_out_trigger_ratios"],
            max_hold_minutes=settings["max_hold_minutes"],
            rationale="Legacy profile thresholds remain active until the dynamic exit engine fully replaces them.",
            conditions=(
                ExitCondition(reason="protect capital with a hard stop", trigger_price=hard_stop_price),
                ExitCondition(reason="take profit only after modeled costs are covered", trigger_price=profit_target_price, requires_profit=True),
            ),
        )

    def _decision_explanation(
        self,
        regime: MarketRegimeAssessment,
        setup: TradeSetup,
        order_request: OrderRequest,
        exit_plan: ExitPlan,
        diagnostics: RollingSignalDiagnostics,
        position: NormalizedPosition | None,
    ) -> DecisionExplanation:
        warnings: list[str] = []
        if exit_plan.max_hold_minutes is not None:
            warnings.append("Legacy max-hold is still active as a safety rail in the scanner path.")
        if position is not None:
            warnings.append("This signal is managing an existing position, not opening a fresh trade.")
        return DecisionExplanation(
            summary=setup.rationale,
            reasons=(regime.rationale, setup.rationale, exit_plan.rationale),
            warnings=tuple(warnings),
            evidence=(
                SignalEvidence(label="side", value=order_request.side.value),
                SignalEvidence(label="delta_ratio", value=f"{diagnostics.delta_ratio:.4f}"),
                SignalEvidence(label="baseline_ratio", value=f"{diagnostics.baseline_ratio:.4f}"),
                SignalEvidence(label="long_votes", value=str(diagnostics.long_votes)),
                SignalEvidence(label="short_votes", value=str(diagnostics.short_votes)),
            ),
        )

    def _managed_exit_setup(
        self,
        context: StrategyContext,
        position: NormalizedPosition,
        diagnostics: RollingSignalDiagnostics,
    ) -> TradeSetup | None:
        settings = profile_settings(self.profile_id)
        gross_pnl_ratio = _position_pnl_ratio(position, diagnostics.current_price)
        net_pnl_ratio = gross_pnl_ratio - estimated_round_trip_cost_ratio(context.market)
        close_side = OrderSide.SELL if position.quantity > 0 else OrderSide.BUY

        if gross_pnl_ratio <= -settings["stop_loss_ratio"]:
            return self._trade_setup(close_side, "hard stop-loss triggered", diagnostics, position)
        if net_pnl_ratio >= settings["min_net_profit_ratio"]:
            return self._trade_setup(close_side, "take-profit captured above estimated costs", diagnostics, position)
        if context.position_opened_at is not None:
            held_for = context.evaluated_at - context.position_opened_at
            if held_for >= timedelta(minutes=settings["max_hold_minutes"]):
                return self._trade_setup(close_side, "max hold time reached", diagnostics, position)
        if position.quantity > 0 and diagnostics.baseline_ratio <= Decimal("-0.008"):
            return self._trade_setup(OrderSide.SELL, "existing long weakened below baseline", diagnostics, position)
        if position.quantity < 0 and diagnostics.baseline_ratio >= Decimal("0.008"):
            return self._trade_setup(OrderSide.BUY, "existing short weakened above baseline", diagnostics, position)
        return None

    def _trade_setup(
        self,
        side: OrderSide,
        rationale: str,
        diagnostics: RollingSignalDiagnostics,
        position: NormalizedPosition | None,
    ) -> TradeSetup:
        family = SetupFamily.MOMENTUM
        if position is not None:
            family = SetupFamily.DEFENSIVE_EXIT
        elif self.profile.strategy_id == "breakout":
            family = SetupFamily.BREAKOUT_CONTINUATION
        elif self.profile.strategy_id == "mean_reversion":
            family = SetupFamily.MEAN_REVERSION
        elif self.profile.strategy_id == "ml_ensemble":
            if diagnostics.breakout_up or diagnostics.breakout_down:
                family = SetupFamily.BREAKOUT_CONTINUATION
            elif diagnostics.reversion_up or diagnostics.reversion_down:
                family = SetupFamily.MEAN_REVERSION

        direction = "long" if side == OrderSide.BUY else "short"
        if position is not None and position.quantity > 0:
            direction = "close-long"
        elif position is not None and position.quantity < 0:
            direction = "close-short"

        confidence = min(
            Decimal("0.95"),
            Decimal("0.25") + max(abs(diagnostics.delta_ratio), abs(diagnostics.baseline_ratio)) * Decimal("50"),
        )
        return TradeSetup(
            setup_id=f"{self.profile.strategy_id}-{self.symbol.lower().replace('/', '-')}",
            family=family,
            symbol=self.symbol,
            direction=direction,
            confidence=confidence,
            rationale=rationale,
            supporting_factors=(
                f"delta_ratio={diagnostics.delta_ratio:.4f}",
                f"baseline_ratio={diagnostics.baseline_ratio:.4f}",
            ),
            time_horizon_minutes=profile_settings(self.profile_id)["max_hold_minutes"],
        )

    def _prepend_regime_detail(
        self,
        diagnostics: RollingSignalDiagnostics,
        details: tuple[str, ...],
    ) -> tuple[str, ...]:
        regime = self._regime_assessment(diagnostics)
        return (f"regime={regime.regime.value} confidence={regime.confidence:.2f}", *details)


def estimated_round_trip_cost_ratio(market: Market) -> Decimal:
    if market == Market.CRYPTO:
        return Decimal("0.0012")
    if market == Market.FOREX:
        return Decimal("0.0008")
    return Decimal("0.0015")


def adaptive_scale_management(
    *,
    strategy_id: str,
    profile_id: str,
    min_profit_ratio: Decimal,
) -> dict[str, tuple[Decimal, ...]]:
    base_ratio_map: dict[str, tuple[Decimal, Decimal]] = {
        "conservative": (Decimal("0.60"), Decimal("0.40")),
        "moderate": (Decimal("0.50"), Decimal("0.50")),
        "aggressive": (Decimal("0.35"), Decimal("0.65")),
        "hft": (Decimal("0.70"), Decimal("0.30")),
    }
    base_trigger_multiplier_map: dict[str, tuple[Decimal, Decimal]] = {
        "conservative": (Decimal("0.85"), Decimal("1.35")),
        "moderate": (Decimal("1.00"), Decimal("1.70")),
        "aggressive": (Decimal("1.20"), Decimal("2.20")),
        "hft": (Decimal("0.60"), Decimal("1.00")),
    }
    ratio_shift_map: dict[str, Decimal] = {
        "breakout": Decimal("-0.10"),
        "mean_reversion": Decimal("0.10"),
        "ml_ensemble": Decimal("-0.05"),
    }
    trigger_shift_map: dict[str, tuple[Decimal, Decimal]] = {
        "breakout": (Decimal("0.20"), Decimal("0.35")),
        "mean_reversion": (Decimal("-0.15"), Decimal("-0.30")),
        "ml_ensemble": (Decimal("0.10"), Decimal("0.25")),
    }

    base_ratios = base_ratio_map.get(profile_id, base_ratio_map["moderate"])
    base_trigger_multipliers = base_trigger_multiplier_map.get(profile_id, base_trigger_multiplier_map["moderate"])
    ratio_shift = ratio_shift_map.get(strategy_id, Decimal("0"))
    trigger_shifts = trigger_shift_map.get(strategy_id, (Decimal("0"), Decimal("0")))

    first_ratio = _clamp_ratio(base_ratios[0] + ratio_shift, minimum=Decimal("0.25"), maximum=Decimal("0.80"))
    second_ratio = _clamp_ratio(base_ratios[1] - ratio_shift, minimum=Decimal("0.20"), maximum=Decimal("0.80"))
    first_trigger_ratio = _normalized_trigger_ratio(min_profit_ratio * (base_trigger_multipliers[0] + trigger_shifts[0]))
    second_trigger_ratio = _normalized_trigger_ratio(min_profit_ratio * (base_trigger_multipliers[1] + trigger_shifts[1]))

    return {
        "scale_out_ratios": (first_ratio, second_ratio),
        "scale_out_trigger_ratios": (first_trigger_ratio, second_trigger_ratio),
    }


def _clamp_ratio(value: Decimal, *, minimum: Decimal, maximum: Decimal) -> Decimal:
    return max(minimum, min(maximum, value)).quantize(Decimal("0.0001"))


def _normalized_trigger_ratio(value: Decimal) -> Decimal:
    return max(Decimal("0.0005"), value).quantize(Decimal("0.0001"))


def profile_settings(profile_id: str) -> ProfileSettings:
    profiles: dict[str, ProfileSettings] = {
        "conservative": {
            "target_notional": Decimal("350"),
            "threshold_bias": Decimal("0.0015"),
            "breakout_buffer": Decimal("0.0006"),
            "cooldown_seconds": 420,
            "stop_loss_ratio": Decimal("0.009"),
            "min_net_profit_ratio": Decimal("0.004"),
            "max_hold_minutes": 480,
        },
        "moderate": {
            "target_notional": Decimal("650"),
            "threshold_bias": Decimal("0.0000"),
            "breakout_buffer": Decimal("0.0000"),
            "cooldown_seconds": 300,
            "stop_loss_ratio": Decimal("0.012"),
            "min_net_profit_ratio": Decimal("0.006"),
            "max_hold_minutes": 360,
        },
        "aggressive": {
            "target_notional": Decimal("1000"),
            "threshold_bias": Decimal("-0.0008"),
            "breakout_buffer": Decimal("0.0005"),
            "cooldown_seconds": 180,
            "stop_loss_ratio": Decimal("0.018"),
            "min_net_profit_ratio": Decimal("0.009"),
            "max_hold_minutes": 240,
        },
        "hft": {
            "target_notional": Decimal("250"),
            "threshold_bias": Decimal("-0.0012"),
            "breakout_buffer": Decimal("0.0008"),
            "cooldown_seconds": 90,
            "stop_loss_ratio": Decimal("0.006"),
            "min_net_profit_ratio": Decimal("0.003"),
            "max_hold_minutes": 75,
        },
    }
    default_profile: ProfileSettings = {
        "target_notional": Decimal("500"),
        "threshold_bias": Decimal("0.0000"),
        "breakout_buffer": Decimal("0.0000"),
        "cooldown_seconds": 300,
        "stop_loss_ratio": Decimal("0.012"),
        "min_net_profit_ratio": Decimal("0.006"),
        "max_hold_minutes": 360,
    }
    return profiles.get(profile_id, default_profile)


def _position_pnl_ratio(position: NormalizedPosition, current_price: Decimal) -> Decimal:
    if position.average_price <= Decimal("0"):
        return Decimal("0")
    if position.quantity >= 0:
        return (current_price - position.average_price) / position.average_price
    return (position.average_price - current_price) / position.average_price


def _client_order_id(market: Market, symbol: str, strategy_id: str) -> str:
    timestamp = int(datetime.now(UTC).timestamp())
    market_code = market.value[:2]
    safe_symbol = "".join(character for character in symbol.lower() if character.isalnum())[:8] or "symbol"
    strategy_code = "".join(character for character in strategy_id.lower() if character.isalnum())[:4] or "strt"
    return f"sc-{market_code}-{safe_symbol}-{strategy_code}-{timestamp}"


def _yes_no(value: bool) -> str:
    return "yes" if value else "no"