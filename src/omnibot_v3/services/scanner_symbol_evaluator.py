"""Helpers for evaluating ranked scanner symbols and producing execution candidates."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from omnibot_v3.domain import (
    CandidateSelection,
    HistoricalBar,
    Market,
    NormalizedAccount,
    NormalizedPosition,
    OrderRequest,
    OrderSide,
    OrderType,
    StrategyCandidate,
    StrategyContext,
    StrategyExecutionResult,
    StrategyProfile,
    StrategySignal,
    TradeThesis,
    build_trade_thesis,
)
from omnibot_v3.services.risk_engine import StrategyRuntime
from omnibot_v3.services.rolling_decision_support import (
    RollingLegacyDecisionSupport,
    RollingSignalDiagnostics,
    build_rolling_layered_plugin,
    result_detail_lines,
)
from omnibot_v3.services.scanner_feedback import (
    ScannerFeedback,
    accepted_result_payload,
    analysis_skip_feedback,
    cooldown_blocked_feedback,
    execution_blocked_feedback,
    execution_summary,
    quote_missing_feedback,
    risk_rejected_feedback,
    signal_accepted_feedback,
)
from omnibot_v3.services.scanner_market_policy import policy_for_market
from omnibot_v3.services.scanner_runtime_support import (
    build_risk_engine,
    build_strategy_profile,
    order_quantity,
)


def _feedback_priority(feedback: ScannerFeedback) -> int:
    priorities = {
        "risk-rejected": 3,
        "execution-blocked": 2,
        "cooldown-blocked": 2,
        "analysis-skip": 1,
    }
    return priorities.get(feedback.event_type, 0)


def _candidate_score(result: StrategyExecutionResult) -> Decimal:
    result_confidence = _normalized_confidence(result.confidence)
    setup_confidence = _normalized_confidence(result.setup.confidence if result.setup is not None else None)
    regime_confidence = _normalized_confidence(result.regime.confidence if result.regime is not None else None)
    confidence_values = tuple(
        value for value in (result_confidence, setup_confidence, regime_confidence) if value > Decimal("0")
    )

    weighted_confidence = (
        result_confidence * Decimal("0.45")
        + setup_confidence * Decimal("0.35")
        + regime_confidence * Decimal("0.20")
    ) * Decimal("15")
    agreement_bonus = min(confidence_values) * Decimal("2") if confidence_values else Decimal("0")
    explanation_bonus = Decimal("0")
    if result.explanation is not None:
        explanation_bonus = min(Decimal(len(result.explanation.reasons)), Decimal("3")) / Decimal("100")
    return weighted_confidence + agreement_bonus + explanation_bonus


def _normalized_confidence(value: Decimal | None) -> Decimal:
    if value is None:
        return Decimal("0")
    return max(Decimal("0"), min(Decimal("1"), value))


def _candidate_evidence(details: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(detail for detail in details if not detail.startswith("candidate_score="))


def _thesis_payload(thesis: TradeThesis) -> dict[str, object]:
    return {
        "thesis_id": thesis.thesis_id,
        "symbol": thesis.symbol,
        "strategy_id": thesis.strategy_id,
        "profile_id": thesis.profile_id,
        "side": thesis.order_request.side.value,
        "score": str(thesis.score.quantize(Decimal("0.0001"))),
        "confidence": str(thesis.confidence) if thesis.confidence is not None else None,
        "setup_family": thesis.setup.family.value if thesis.setup is not None else None,
        "regime": thesis.regime.regime.value if thesis.regime is not None else None,
        "summary": thesis.explanation.summary if thesis.explanation is not None else thesis.rationale,
        "created_at": thesis.created_at.isoformat(),
        "exit_plan": _exit_plan_payload(thesis),
        "lifecycle_state": "candidate-selected",
        "lifecycle_reason": "candidate selected by autonomous evaluator",
        "last_transition_at": thesis.created_at.isoformat(),
        "evidence": list(thesis.evidence),
    }


def _candidate_payload(candidate: StrategyCandidate) -> dict[str, object]:
    thesis = build_trade_thesis(candidate)
    return {
        "thesis_id": thesis.thesis_id,
        "symbol": candidate.symbol,
        "strategy_id": candidate.strategy_id,
        "profile_id": candidate.profile_id,
        "side": candidate.order_request.side.value,
        "score": str(candidate.score.quantize(Decimal("0.0001"))),
        "confidence": str(candidate.confidence) if candidate.confidence is not None else None,
        "setup_family": candidate.setup.family.value if candidate.setup is not None else None,
        "regime": candidate.regime.regime.value if candidate.regime is not None else None,
        "summary": candidate.explanation.summary if candidate.explanation is not None else candidate.rationale,
        "created_at": candidate.created_at.isoformat(),
        "exit_plan": _exit_plan_payload(candidate),
        "evidence": list(candidate.evidence),
    }


def _candidate_payloads(candidates: tuple[StrategyCandidate, ...]) -> list[dict[str, object]]:
    ranked = sorted(candidates, key=lambda candidate: (candidate.score, candidate.strategy_id), reverse=True)
    return [_candidate_payload(candidate) for candidate in ranked]


def _exit_plan_payload(candidate_or_thesis: StrategyCandidate | TradeThesis) -> dict[str, object] | None:
    exit_plan = candidate_or_thesis.exit_plan
    if exit_plan is None:
        return None
    return {
        "hard_stop_price": str(exit_plan.hard_stop_price) if exit_plan.hard_stop_price is not None else None,
        "profit_target_price": str(exit_plan.profit_target_price) if exit_plan.profit_target_price is not None else None,
        "trailing_stop_ratio": str(exit_plan.trailing_stop_ratio) if exit_plan.trailing_stop_ratio is not None else None,
        "scale_out_ratio": str(exit_plan.scale_out_ratio) if exit_plan.scale_out_ratio is not None else None,
        "scale_out_ratios": [str(ratio) for ratio in exit_plan.scale_out_ratios],
        "scale_out_trigger_ratios": [str(ratio) for ratio in exit_plan.scale_out_trigger_ratios],
        "max_hold_minutes": exit_plan.max_hold_minutes,
        "rationale": exit_plan.rationale,
    }


def autonomous_strategy_ids_for_market(market: Market) -> tuple[str, ...]:
    if market == Market.STOCKS:
        return ("momentum", "breakout", "mean_reversion")
    if market == Market.CRYPTO:
        return ("breakout", "momentum", "ml_ensemble")
    if market == Market.FOREX:
        return ("mean_reversion", "momentum", "breakout")
    return ("momentum",)


@dataclass(frozen=True, slots=True)
class RollingSignalPlugin:
    profile: StrategyProfile
    profile_id: str
    symbol: str
    recent_prices: tuple[Decimal, ...]
    quantity: Decimal
    recent_bars: tuple[HistoricalBar, ...] = ()
    allow_short: bool = False

    def _support(self) -> RollingLegacyDecisionSupport:
        return RollingLegacyDecisionSupport(
            profile=self.profile,
            profile_id=self.profile_id,
            symbol=self.symbol,
            recent_prices=self.recent_prices,
            quantity=self.quantity,
            recent_bars=self.recent_bars,
            allow_short=self.allow_short,
        )

    def generate_signal(self, context: StrategyContext) -> StrategySignal | None:
        if len(self.recent_prices) < 3 or context.latest_price is None:
            return None

        support = self._support()
        diagnostics = support.signal_diagnostics()
        if diagnostics is None:
            return None
        return self._layered_plugin(support, diagnostics, support.position_for(context)).generate_signal(context)

    def _layered_plugin(
        self,
        support: RollingLegacyDecisionSupport,
        diagnostics: RollingSignalDiagnostics,
        position: NormalizedPosition | None,
    ):
        return build_rolling_layered_plugin(self.profile, support, diagnostics, position)

    def explain_no_signal(self, context: StrategyContext) -> tuple[str, ...]:
        return self._support().explain_no_signal(context)

    def planned_exit_price(self, context: StrategyContext) -> Decimal | None:
        return self._support().planned_exit_price(context)


@dataclass(frozen=True, slots=True)
class _AcceptedCandidate:
    candidate: StrategyCandidate
    result: StrategyExecutionResult
    details: tuple[str, ...]
    strategy_rank: int


@dataclass(slots=True)
class ScannerSymbolEvaluator:
    market: Market
    account: NormalizedAccount
    positions: tuple[NormalizedPosition, ...]
    profile_id: str
    latest_prices: dict[str, Decimal]
    ordered_symbols: tuple[str, ...]
    observed_at: datetime
    record_live_bar: Callable[[Market, str, Decimal, datetime], None]
    history_for: Callable[[Market, str], deque[Decimal]]
    recent_bars_for: Callable[[Market, str, int], tuple[HistoricalBar, ...]]
    position_opened_at_for: Callable[[str], datetime | None]
    selected_thesis_for: Callable[[str], dict[str, object] | None]
    record_feedback: Callable[[ScannerFeedback, str, Decimal | None, str], None]
    cooldown_active: Callable[[str], bool]
    mark_trade_at: Callable[[str, datetime], None]
    learning_adjustment_for: Callable[[str, str, StrategyExecutionResult], tuple[Decimal, tuple[str, ...]]] = (
        lambda symbol, strategy_id, result: (Decimal("0"), ())
    )

    def evaluate(self) -> dict[str, object]:
        latest_decision = f"Scanning {len(self.ordered_symbols)} ranked symbols."
        fallback_result: dict[str, object] | None = None
        fallback_priority = -1
        market_policy = policy_for_market(self.market)
        active_symbols = {position.symbol.upper() for position in self.positions}
        default_strategy_ids = autonomous_strategy_ids_for_market(self.market)

        for symbol in self.ordered_symbols:
            price = self.latest_prices.get(symbol)
            if price is None or price <= Decimal("0"):
                latest_decision = f"{symbol}: no quote available."
                self.record_feedback(quote_missing_feedback(symbol), symbol, None, "auto")
                continue

            self.record_live_bar(self.market, symbol, price, self.observed_at)
            history = self.history_for(self.market, symbol)
            history.append(price)
            recent_bars = self.recent_bars_for(self.market, symbol, 12)
            position_opened_at = self.position_opened_at_for(symbol) if symbol.upper() in active_symbols else None
            active_thesis = self.selected_thesis_for(symbol) if symbol.upper() in active_symbols else None
            active_position = next((position for position in self.positions if position.symbol.upper() == symbol.upper()), None)
            active_profile_id = self.profile_id
            strategy_ids = default_strategy_ids
            if isinstance(active_thesis, dict):
                thesis_strategy_id = str(active_thesis.get("strategy_id") or "").strip().lower()
                thesis_profile_id = str(active_thesis.get("profile_id") or self.profile_id).strip().lower()
                if thesis_strategy_id:
                    strategy_ids = (thesis_strategy_id,)
                if thesis_profile_id:
                    active_profile_id = thesis_profile_id

            thesis_exit = self._thesis_exit_result(
                symbol=symbol,
                price=price,
                position=active_position,
                active_thesis=active_thesis,
                position_opened_at=position_opened_at,
            )
            if thesis_exit is not None:
                latest_decision = str(thesis_exit.get("decision") or latest_decision)
                thesis_order_request = thesis_exit.get("order_request")
                if isinstance(thesis_order_request, OrderRequest):
                    raw_details = thesis_exit.get("details", ())
                    thesis_details = (
                        tuple(str(detail) for detail in raw_details)
                        if isinstance(raw_details, (list, tuple))
                        else ()
                    )
                    self.mark_trade_at(symbol, datetime.now(UTC))
                    self.record_feedback(
                        signal_accepted_feedback(
                            symbol,
                            price,
                            thesis_order_request.side,
                            thesis_details,
                        ),
                        symbol,
                        price,
                        str(thesis_exit.get("strategy_id") or "thesis"),
                    )
                return thesis_exit
            risk_engine = build_risk_engine(self.market, active_profile_id)
            context = StrategyContext(
                market=self.market,
                account=self.account,
                positions=self.positions,
                latest_price=price,
                bar_timestamp=datetime.now(UTC),
                position_opened_at=position_opened_at,
            )
            accepted_candidates: list[_AcceptedCandidate] = []
            for strategy_rank, strategy_id in enumerate(strategy_ids):
                profile = build_strategy_profile(self.market, strategy_id, active_profile_id)
                plugin = RollingSignalPlugin(
                    profile=profile,
                    profile_id=active_profile_id,
                    symbol=symbol,
                    recent_prices=tuple(history),
                    recent_bars=recent_bars,
                    quantity=order_quantity(self.market, active_profile_id, price),
                    allow_short=market_policy.allow_short,
                )
                runtime = StrategyRuntime(plugin=plugin, risk_engine=risk_engine)
                result = runtime.evaluate(context)
                details = (f"strategy={strategy_id}", *result_detail_lines(result))
                latest_decision = execution_summary(symbol, result.decision.reason)

                if result.order_request is None:
                    if result.decision.reason == "no signal generated":
                        feedback = analysis_skip_feedback(
                            symbol,
                            price,
                            result.decision.reason,
                            (f"strategy={strategy_id}", *plugin.explain_no_signal(context)),
                        )
                    else:
                        feedback = risk_rejected_feedback(
                            symbol,
                            price,
                            result.decision.reason,
                            details,
                        )
                    self.record_feedback(feedback, symbol, price, strategy_id)
                    if feedback.fallback_result is not None and _feedback_priority(feedback) >= fallback_priority:
                        fallback_result = feedback.fallback_result
                        fallback_priority = _feedback_priority(feedback)
                    if feedback.event_type != "analysis-skip":
                        break
                    continue

                if not market_policy.can_submit_for_symbol(symbol, self.positions, result.order_request):
                    feedback = execution_blocked_feedback(symbol, price)
                    self.record_feedback(feedback, symbol, price, strategy_id)
                    if feedback.fallback_result is not None and _feedback_priority(feedback) >= fallback_priority:
                        fallback_result = feedback.fallback_result
                        fallback_priority = _feedback_priority(feedback)
                    break

                if self.cooldown_active(symbol):
                    feedback = cooldown_blocked_feedback(symbol, price)
                    self.record_feedback(feedback, symbol, price, strategy_id)
                    if feedback.fallback_result is not None and _feedback_priority(feedback) >= fallback_priority:
                        fallback_result = feedback.fallback_result
                        fallback_priority = _feedback_priority(feedback)
                    break

                base_candidate_score = _candidate_score(result)
                learning_score_delta, learning_details = self.learning_adjustment_for(symbol, strategy_id, result)
                candidate_score = base_candidate_score + learning_score_delta
                candidate_details = (
                    f"candidate_base_score={base_candidate_score:.4f}",
                    f"candidate_score={candidate_score:.4f}",
                    *learning_details,
                    *details,
                )
                candidate = StrategyCandidate(
                    symbol=symbol,
                    strategy_id=strategy_id,
                    profile_id=active_profile_id,
                    order_request=result.order_request,
                    score=candidate_score,
                    confidence=result.confidence,
                    regime=result.regime,
                    setup=result.setup,
                    exit_plan=result.exit_plan,
                    explanation=result.explanation,
                    rationale=result.decision.reason,
                    evidence=_candidate_evidence(candidate_details),
                )
                accepted_candidates.append(
                    _AcceptedCandidate(
                        candidate=candidate,
                        result=result,
                        details=candidate_details,
                        strategy_rank=strategy_rank,
                    )
                )

            if accepted_candidates:
                best_candidate = max(
                    accepted_candidates,
                    key=lambda candidate: (
                        candidate.candidate.score,
                        candidate.result.confidence or Decimal("0"),
                        candidate.result.setup.confidence if candidate.result.setup is not None else Decimal("0"),
                        candidate.result.regime.confidence if candidate.result.regime is not None else Decimal("0"),
                        -candidate.strategy_rank,
                    ),
                )
                latest_decision = execution_summary(
                    symbol,
                    f"selected {best_candidate.candidate.strategy_id} from {len(accepted_candidates)} viable setup(s)",
                )
                order_request = best_candidate.result.order_request
                assert order_request is not None
                selection = CandidateSelection(
                    symbol=symbol,
                    selected=best_candidate.candidate,
                    considered=tuple(candidate.candidate for candidate in accepted_candidates),
                )
                selected_thesis = build_trade_thesis(selection.selected)
                self.mark_trade_at(symbol, datetime.now(UTC))
                self.record_feedback(
                    signal_accepted_feedback(
                        symbol,
                        price,
                        order_request.side,
                        best_candidate.details,
                    ),
                    symbol,
                    price,
                    best_candidate.candidate.strategy_id,
                )
                accepted = accepted_result_payload(
                    symbol,
                    order_request.side,
                    order_request,
                    price,
                    best_candidate.details,
                )
                accepted["strategy_id"] = best_candidate.candidate.strategy_id
                accepted["candidate_count"] = len(accepted_candidates)
                accepted["candidate_score"] = str(best_candidate.candidate.score.quantize(Decimal("0.0001")))
                accepted["selected_candidate"] = _candidate_payload(selection.selected)
                accepted["selected_thesis"] = _thesis_payload(selected_thesis)
                accepted["considered_candidates"] = _candidate_payloads(selection.considered)
                accepted["decision"] = latest_decision
                return accepted

        if fallback_result is not None:
            return fallback_result
        return {"decision": latest_decision}

    def _thesis_exit_result(
        self,
        *,
        symbol: str,
        price: Decimal,
        position: NormalizedPosition | None,
        active_thesis: dict[str, object] | None,
        position_opened_at: datetime | None,
    ) -> dict[str, object] | None:
        if position is None or not isinstance(active_thesis, dict):
            return None
        exit_plan = active_thesis.get("exit_plan")
        if not isinstance(exit_plan, dict):
            return None

        hard_stop_price = _decimal_from_payload(exit_plan.get("hard_stop_price"))
        profit_target_price = _decimal_from_payload(exit_plan.get("profit_target_price"))
        trailing_stop_ratio = _normalized_trailing_ratio(exit_plan.get("trailing_stop_ratio"))
        scale_out_ratio = _normalized_scale_out_ratio(exit_plan.get("scale_out_ratio"))
        scale_out_ratios = _normalized_scale_out_ratios(exit_plan.get("scale_out_ratios"), fallback=scale_out_ratio)
        scale_out_trigger_ratios = _normalized_stage_trigger_ratios(
            exit_plan.get("scale_out_trigger_ratios"),
            stage_count=len(scale_out_ratios),
            fallback=trailing_stop_ratio,
        )
        max_hold_minutes = _int_from_payload(exit_plan.get("max_hold_minutes"))
        trailing_anchor_price = _decimal_from_payload(active_thesis.get("trailing_anchor_price"))
        trailing_stop_price = _decimal_from_payload(active_thesis.get("trailing_stop_price"))
        scale_out_count = _int_from_payload(active_thesis.get("scale_out_count")) or 0
        last_scale_out_price = _decimal_from_payload(active_thesis.get("last_scale_out_price"))
        total_scaled_out_quantity = _decimal_from_payload(active_thesis.get("total_scaled_out_quantity")) or Decimal("0")
        entry_price = position.average_price if position.average_price > Decimal("0") else None
        is_long = position.quantity > 0
        trigger_reason: str | None = None
        thesis_update_reason: str | None = None

        if trailing_stop_ratio is not None and trailing_stop_price is not None:
            if is_long and price <= trailing_stop_price:
                trigger_reason = f"thesis trailing stop reached at {price}"
            elif not is_long and price >= trailing_stop_price:
                trigger_reason = f"thesis trailing stop reached at {price}"

        if trigger_reason is None and is_long:
            if hard_stop_price is not None and price <= hard_stop_price:
                trigger_reason = f"thesis hard stop reached at {price}"
            elif profit_target_price is not None and trailing_stop_ratio is None and price >= profit_target_price:
                trigger_reason = f"thesis profit target reached at {price}"
        elif trigger_reason is None and position.quantity < 0:
            if hard_stop_price is not None and price >= hard_stop_price:
                trigger_reason = f"thesis hard stop reached at {price}"
            elif profit_target_price is not None and trailing_stop_ratio is None and price <= profit_target_price:
                trigger_reason = f"thesis profit target reached at {price}"

        if trigger_reason is None and trailing_stop_ratio is not None:
            activation_price = profit_target_price
            if activation_price is None and entry_price is not None:
                activation_price = entry_price * (
                    Decimal("1") + trailing_stop_ratio if is_long else Decimal("1") - trailing_stop_ratio
                )

            current_scale_out_ratio = scale_out_ratios[scale_out_count] if scale_out_count < len(scale_out_ratios) else None
            scale_out_quantity = None
            if current_scale_out_ratio is not None:
                scale_out_quantity = _scaled_exit_quantity(abs(position.quantity), current_scale_out_ratio)

            stage_trigger_ratio = (
                scale_out_trigger_ratios[scale_out_count]
                if scale_out_count < len(scale_out_trigger_ratios)
                else None
            )
            stage_activation_price = activation_price
            if stage_trigger_ratio is not None:
                reference_price = last_scale_out_price if scale_out_count > 0 and last_scale_out_price is not None else entry_price
                if reference_price is not None:
                    stage_activation_price = _next_scale_out_trigger_price(
                        reference_price=reference_price,
                        trailing_stop_ratio=stage_trigger_ratio,
                        is_long=is_long,
                    )
            elif scale_out_count > 0 and last_scale_out_price is not None and trailing_stop_ratio is not None:
                stage_activation_price = _next_scale_out_trigger_price(
                    reference_price=last_scale_out_price,
                    trailing_stop_ratio=trailing_stop_ratio,
                    is_long=is_long,
                )

            if (
                scale_out_quantity is not None
                and stage_activation_price is not None
                and _favorable_move_reached(
                    price=price,
                    threshold_price=stage_activation_price,
                    is_long=is_long,
                )
            ):
                remaining_quantity = abs(position.quantity) - scale_out_quantity
                updated_thesis = dict(active_thesis)
                updated_thesis["scale_out_count"] = scale_out_count + 1
                updated_thesis["remaining_quantity"] = str(remaining_quantity)
                updated_thesis["scaled_out_quantity"] = str(scale_out_quantity)
                updated_thesis["total_scaled_out_quantity"] = str(total_scaled_out_quantity + scale_out_quantity)
                updated_thesis["last_scale_out_price"] = str(price)
                updated_thesis["scale_out_stage"] = scale_out_count + 1
                updated_thesis["scale_out_stage_total"] = len(scale_out_ratios)
                if trailing_stop_ratio is not None:
                    trailing_anchor_price = price
                    trailing_stop_price = _trailing_stop_from_anchor(
                        anchor_price=trailing_anchor_price,
                        trailing_stop_ratio=trailing_stop_ratio,
                        is_long=is_long,
                    )
                    updated_thesis["trailing_anchor_price"] = str(trailing_anchor_price)
                    updated_thesis["trailing_stop_price"] = str(trailing_stop_price)
                    updated_thesis["trailing_armed_at"] = str(active_thesis.get("trailing_armed_at") or self.observed_at.isoformat())
                next_stage_trigger_ratio = (
                    scale_out_trigger_ratios[scale_out_count + 1]
                    if scale_out_count + 1 < len(scale_out_trigger_ratios)
                    else trailing_stop_ratio
                )
                if scale_out_count + 1 < len(scale_out_ratios) and next_stage_trigger_ratio is not None:
                    updated_thesis["next_scale_out_trigger_price"] = str(
                        _next_scale_out_trigger_price(
                            reference_price=price,
                            trailing_stop_ratio=next_stage_trigger_ratio,
                            is_long=is_long,
                        )
                    )
                else:
                    updated_thesis.pop("next_scale_out_trigger_price", None)
                scale_out_reason = f"scaled out stage {scale_out_count + 1}/{len(scale_out_ratios)} for {scale_out_quantity} at {price}"
                if trailing_stop_price is not None:
                    scale_out_reason = f"{scale_out_reason}; trailing stop armed at {trailing_stop_price:.4f}"
                updated_thesis["lifecycle_state"] = "scaled-out-active"
                updated_thesis["lifecycle_reason"] = scale_out_reason
                updated_thesis["last_transition_at"] = self.observed_at.isoformat()
                order_request = OrderRequest(
                    client_order_id=f"thesis-scale-out-{uuid4().hex[:10]}",
                    symbol=symbol,
                    side=OrderSide.SELL if is_long else OrderSide.BUY,
                    quantity=scale_out_quantity,
                    order_type=OrderType.MARKET,
                    limit_price=price,
                )
                details = tuple(
                    detail
                    for detail in (
                        f"strategy={str(active_thesis.get('strategy_id') or 'thesis')}",
                        f"thesis_id={str(active_thesis.get('thesis_id') or '')}",
                        f"thesis_scale_out={scale_out_reason}",
                        f"scale_out_ratio={current_scale_out_ratio:.4f}",
                        f"scale_out_trigger_ratio={stage_trigger_ratio:.4f}" if stage_trigger_ratio is not None else None,
                        f"scale_out_stage={scale_out_count + 1}/{len(scale_out_ratios)}",
                        f"scale_out_quantity={scale_out_quantity}",
                        f"remaining_quantity={remaining_quantity}",
                        f"next_scale_out_trigger={updated_thesis.get('next_scale_out_trigger_price')}",
                        f"trailing_stop={trailing_stop_price}" if trailing_stop_price is not None else None,
                    )
                    if detail is not None
                )
                return {
                    **accepted_result_payload(symbol, order_request.side, order_request, price, details),
                    "strategy_id": str(active_thesis.get("strategy_id") or "thesis"),
                    "candidate_count": 1,
                    "candidate_score": str(active_thesis.get("score") or "0"),
                    "selected_candidate": None,
                    "selected_thesis": updated_thesis,
                    "considered_candidates": [],
                    "thesis_transition_state": "scale-out-submitted",
                    "thesis_transition_reason": scale_out_reason,
                    "decision": execution_summary(symbol, scale_out_reason),
                }

            if trailing_anchor_price is None and activation_price is not None and _favorable_move_reached(
                price=price,
                threshold_price=activation_price,
                is_long=is_long,
            ):
                trailing_anchor_price = price
                trailing_stop_price = _trailing_stop_from_anchor(
                    anchor_price=trailing_anchor_price,
                    trailing_stop_ratio=trailing_stop_ratio,
                    is_long=is_long,
                )
                thesis_update_reason = (
                    f"trailing stop armed at {trailing_stop_price:.4f} after favorable move to {price}"
                )
            elif (
                trailing_anchor_price is not None
                and _is_more_favorable_price(price=price, anchor_price=trailing_anchor_price, is_long=is_long)
            ):
                trailing_anchor_price = price
                trailing_stop_price = _trailing_stop_from_anchor(
                    anchor_price=trailing_anchor_price,
                    trailing_stop_ratio=trailing_stop_ratio,
                    is_long=is_long,
                )
                thesis_update_reason = f"trailing stop advanced to {trailing_stop_price:.4f} after favorable move to {price}"

        if thesis_update_reason is not None and trailing_anchor_price is not None and trailing_stop_price is not None:
            updated_thesis = dict(active_thesis)
            updated_thesis["trailing_anchor_price"] = str(trailing_anchor_price)
            updated_thesis["trailing_stop_price"] = str(trailing_stop_price)
            updated_thesis["trailing_armed_at"] = str(active_thesis.get("trailing_armed_at") or self.observed_at.isoformat())
            updated_thesis["lifecycle_state"] = "trailing-active"
            updated_thesis["lifecycle_reason"] = thesis_update_reason
            updated_thesis["last_transition_at"] = self.observed_at.isoformat()
            details = tuple(
                detail
                for detail in (
                    f"strategy={str(active_thesis.get('strategy_id') or 'thesis')}",
                    f"thesis_id={str(active_thesis.get('thesis_id') or '')}",
                    f"thesis_update={thesis_update_reason}",
                    f"trailing_anchor={trailing_anchor_price}",
                    f"trailing_stop={trailing_stop_price}",
                    f"trail={trailing_stop_ratio:.4f}",
                    f"target={profit_target_price}" if profit_target_price is not None else None,
                )
                if detail is not None
            )
            return {
                "decision": execution_summary(symbol, thesis_update_reason),
                "signal_symbol": symbol,
                "price": str(price),
                "strategy_id": str(active_thesis.get("strategy_id") or "thesis"),
                "details": details,
                "candidate_count": 0,
                "candidate_score": str(active_thesis.get("score") or "0"),
                "selected_candidate": None,
                "selected_thesis": updated_thesis,
                "considered_candidates": [],
                "thesis_update": updated_thesis,
            }

        if trigger_reason is None and max_hold_minutes is not None and position_opened_at is not None:
            held_minutes = int((self.observed_at - position_opened_at).total_seconds() // 60)
            if held_minutes >= max_hold_minutes:
                trigger_reason = f"thesis max hold reached after {held_minutes} minutes"

        if trigger_reason is None:
            return None

        order_request = OrderRequest(
            client_order_id=f"thesis-exit-{uuid4().hex[:10]}",
            symbol=symbol,
            side=OrderSide.SELL if position.quantity > 0 else OrderSide.BUY,
            quantity=abs(position.quantity),
            order_type=OrderType.MARKET,
            limit_price=price,
        )
        details = tuple(
            detail
            for detail in (
                f"strategy={str(active_thesis.get('strategy_id') or 'thesis')}",
                f"thesis_id={str(active_thesis.get('thesis_id') or '')}",
                f"thesis_exit={trigger_reason}",
                f"stop={hard_stop_price}" if hard_stop_price is not None else None,
                f"target={profit_target_price}" if profit_target_price is not None else None,
                f"trailing_stop={trailing_stop_price}" if trailing_stop_price is not None else None,
                f"trail={trailing_stop_ratio:.4f}" if trailing_stop_ratio is not None else None,
                f"max_hold={max_hold_minutes}m" if max_hold_minutes is not None else None,
            )
            if detail is not None
        )
        return {
            **accepted_result_payload(symbol, order_request.side, order_request, price, details),
            "strategy_id": str(active_thesis.get("strategy_id") or "thesis"),
            "candidate_count": 1,
            "candidate_score": str(active_thesis.get("score") or "0"),
            "selected_candidate": None,
            "selected_thesis": dict(active_thesis),
            "considered_candidates": [],
            "thesis_transition_state": "exit-submitted",
            "thesis_transition_reason": trigger_reason,
            "decision": execution_summary(symbol, trigger_reason),
        }


def _decimal_from_payload(value: object) -> Decimal | None:
    if value is None or value == "":
        return None
    return Decimal(str(value))


def _int_from_payload(value: object) -> int | None:
    if value is None or value == "":
        return None
    return int(str(value))


def _normalized_trailing_ratio(value: object) -> Decimal | None:
    ratio = _decimal_from_payload(value)
    if ratio is None or ratio <= Decimal("0"):
        return None
    return ratio


def _normalized_scale_out_ratio(value: object) -> Decimal | None:
    ratio = _decimal_from_payload(value)
    if ratio is None or ratio <= Decimal("0") or ratio >= Decimal("1"):
        return None
    return ratio


def _normalized_scale_out_ratios(value: object, *, fallback: Decimal | None) -> tuple[Decimal, ...]:
    if isinstance(value, (list, tuple)):
        normalized = tuple(
            ratio
            for item in value
            if (ratio := _normalized_scale_out_ratio(item)) is not None
        )
        if normalized:
            return normalized
    if fallback is not None:
        return (fallback,)
    return ()


def _normalized_stage_trigger_ratios(value: object, *, stage_count: int, fallback: Decimal | None) -> tuple[Decimal, ...]:
    normalized: tuple[Decimal, ...] = ()
    if isinstance(value, (list, tuple)):
        normalized = tuple(
            ratio
            for item in value
            if (ratio := _normalized_trailing_ratio(item)) is not None
        )
    if not normalized and fallback is not None and stage_count > 0:
        normalized = tuple(fallback for _ in range(stage_count))
    elif normalized and stage_count > 0 and len(normalized) < stage_count:
        normalized = (*normalized, *(normalized[-1] for _ in range(stage_count - len(normalized))))
    elif stage_count > 0 and len(normalized) > stage_count:
        normalized = normalized[:stage_count]
    return normalized


def _favorable_move_reached(
    *,
    price: Decimal,
    threshold_price: Decimal,
    is_long: bool,
) -> bool:
    if is_long:
        return price >= threshold_price
    return price <= threshold_price


def _is_more_favorable_price(*, price: Decimal, anchor_price: Decimal, is_long: bool) -> bool:
    if is_long:
        return price > anchor_price
    return price < anchor_price


def _trailing_stop_from_anchor(*, anchor_price: Decimal, trailing_stop_ratio: Decimal, is_long: bool) -> Decimal:
    if is_long:
        return anchor_price * (Decimal("1") - trailing_stop_ratio)
    return anchor_price * (Decimal("1") + trailing_stop_ratio)


def _next_scale_out_trigger_price(*, reference_price: Decimal, trailing_stop_ratio: Decimal, is_long: bool) -> Decimal:
    if is_long:
        return reference_price * (Decimal("1") + trailing_stop_ratio)
    return reference_price * (Decimal("1") - trailing_stop_ratio)


def _scaled_exit_quantity(position_quantity: Decimal, scale_out_ratio: Decimal) -> Decimal | None:
    scaled_quantity = position_quantity * scale_out_ratio
    if scaled_quantity <= Decimal("0") or scaled_quantity >= position_quantity:
        return None
    return scaled_quantity