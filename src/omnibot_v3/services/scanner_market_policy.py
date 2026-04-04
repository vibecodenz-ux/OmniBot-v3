"""Market-specific policy hooks for the live scanner."""

from __future__ import annotations

from dataclasses import dataclass

from omnibot_v3.domain import Market, NormalizedPosition, OrderRequest


@dataclass(frozen=True, slots=True)
class ScannerMarketPolicy:
    market: Market
    allow_short: bool

    def can_submit_for_symbol(
        self,
        symbol: str,
        positions: tuple[NormalizedPosition, ...],
        order_request: OrderRequest,
    ) -> bool:
        del symbol, positions, order_request
        return True


def policy_for_market(market: Market) -> ScannerMarketPolicy:
    return ScannerMarketPolicy(
        market=market,
        allow_short=market in {Market.CRYPTO, Market.FOREX},
    )