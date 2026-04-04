"""Helpers for shaping scanner event messages and fallback evaluation payloads."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from omnibot_v3.domain import OrderRequest, OrderSide


@dataclass(frozen=True, slots=True)
class ScannerFeedback:
    message: str
    event_type: str
    level: str = "info"
    details: tuple[str, ...] = ()
    fallback_result: dict[str, object] | None = None


def execution_summary(symbol: str, message: str) -> str:
    return f"{symbol}: {message}"


def quote_missing_feedback(symbol: str) -> ScannerFeedback:
    return ScannerFeedback(
        message=f"{symbol}: broker price unavailable.",
        event_type="quote-missing",
        level="warning",
    )


def analysis_skip_feedback(symbol: str, price: Decimal, reason: str, details: tuple[str, ...]) -> ScannerFeedback:
    return ScannerFeedback(
        message=f"{symbol}: analysed at {price} and skipped because {reason}.",
        event_type="analysis-skip",
        level="info",
        details=details,
        fallback_result={
            "decision": execution_summary(symbol, reason),
            "price": str(price),
            "signal_symbol": symbol,
        },
    )


def risk_rejected_feedback(symbol: str, price: Decimal, reason: str, details: tuple[str, ...]) -> ScannerFeedback:
    return ScannerFeedback(
        message=f"{symbol}: analysed at {price} and rejected because {reason}.",
        event_type="risk-rejected",
        level="warning",
        details=details,
        fallback_result={
            "decision": execution_summary(symbol, reason),
            "price": str(price),
            "signal_symbol": symbol,
        },
    )


def execution_blocked_feedback(symbol: str, price: Decimal) -> ScannerFeedback:
    return ScannerFeedback(
        message=f"{symbol}: signal generated but execution is disabled for this market.",
        event_type="execution-blocked",
        level="warning",
        fallback_result={
            "decision": execution_summary(symbol, "signal generated but execution is scan-only for this market"),
            "signal_detected": True,
            "signal_symbol": symbol,
            "price": str(price),
        },
    )


def cooldown_blocked_feedback(symbol: str, price: Decimal) -> ScannerFeedback:
    return ScannerFeedback(
        message=f"{symbol}: signal detected at {price} but cooldown is active.",
        event_type="cooldown-blocked",
        level="warning",
        fallback_result={
            "decision": execution_summary(symbol, "signal generated but symbol cooldown is active"),
            "signal_detected": True,
            "signal_symbol": symbol,
            "price": str(price),
        },
    )


def signal_accepted_feedback(symbol: str, price: Decimal, side: OrderSide, details: tuple[str, ...]) -> ScannerFeedback:
    return ScannerFeedback(
        message=f"{symbol}: signal accepted at {price} for {side.value.upper()}.",
        event_type="signal-accepted",
        details=details,
    )


def accepted_result_payload(
    symbol: str,
    side: OrderSide,
    order_request: OrderRequest,
    price: Decimal,
    details: tuple[str, ...],
) -> dict[str, object]:
    return {
        "decision": execution_summary(symbol, f"signal accepted for {side.value.upper()}"),
        "signal_detected": True,
        "signal_symbol": symbol,
        "order_request": order_request,
        "price": str(price),
        "details": details,
    }