"""Trade journal views with broker-backed close actions for the dashboard."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TypedDict
from uuid import uuid4
from zoneinfo import ZoneInfo

from omnibot_v3.domain.broker import (
    BrokerCapability,
    NormalizedTrade,
    OrderRequest,
    OrderSide,
    OrderStatus,
    OrderType,
    PortfolioSnapshot,
)
from omnibot_v3.domain.runtime import Market
from omnibot_v3.services.market_worker import MarketWorker
from omnibot_v3.services.operator_state import OperatorStateService
from omnibot_v3.services.runtime_store import PortfolioSnapshotStore

_NZ = ZoneInfo("Pacific/Auckland")


class BackfilledMarketPayload(TypedDict):
    market: str
    loaded_trade_count: int
    journal_trade_count: int


def utc_now() -> datetime:
    return datetime.now(UTC)


class TradeJournalService:
    def __init__(
        self,
        portfolio_store: PortfolioSnapshotStore,
        workers: dict[Market, MarketWorker],
        operator_state_service: OperatorStateService | None = None,
        thesis_provider: Callable[[Market, str], dict[str, object] | None] | None = None,
    ) -> None:
        self._portfolio_store = portfolio_store
        self._workers = workers
        self._operator_state_service = operator_state_service
        self._thesis_provider = thesis_provider

    def get_journal_payload(self) -> dict[str, object]:
        snapshots = self._portfolio_store.load_all()
        open_positions: list[dict[str, object]] = []
        closed_trades: list[dict[str, object]] = []

        total_unrealized = Decimal("0")
        total_realized = Decimal("0")
        today_realized = Decimal("0")
        yesterday_realized = Decimal("0")
        today_nz = utc_now().astimezone(_NZ).date()
        yesterday_nz = today_nz - timedelta(days=1)
        cleared_before = (
            self._operator_state_service.get_closed_trade_history_cleared_before()
            if self._operator_state_service is not None
            else None
        )

        for market in (Market.STOCKS, Market.CRYPTO, Market.FOREX):
            snapshot = snapshots.get(market)
            if snapshot is None:
                continue

            for position in snapshot.positions:
                total_unrealized += position.unrealized_pnl
                close_available = (
                    BrokerCapability.SUBMIT_ORDER in self._worker_for(market).discover_capabilities()
                )
                selected_thesis = self._thesis_provider(market, position.symbol) if self._thesis_provider is not None else None
                if selected_thesis is None and self._operator_state_service is not None:
                    selected_thesis = self._operator_state_service.get_active_trade_thesis(market, position.symbol)
                open_positions.append(
                    {
                        "market": market.value,
                        "symbol": position.symbol,
                        "status": "open",
                        "side": "buy" if position.quantity >= 0 else "sell",
                        "quantity": str(abs(position.quantity)),
                        "entry_price": str(position.average_price),
                        "market_price": str(position.market_price),
                        "opened_at": position.updated_at.isoformat(),
                        "updated_at": position.updated_at.isoformat(),
                        "unrealized_pnl": str(position.unrealized_pnl),
                        "market_value": str(position.market_value),
                        "thesis_id": selected_thesis.get("thesis_id") if isinstance(selected_thesis, dict) else None,
                        "strategy_id": selected_thesis.get("strategy_id") if isinstance(selected_thesis, dict) else None,
                        "thesis_state": selected_thesis.get("lifecycle_state") if isinstance(selected_thesis, dict) else None,
                        "thesis_reason": selected_thesis.get("lifecycle_reason") if isinstance(selected_thesis, dict) else None,
                        "thesis_transition_at": selected_thesis.get("last_transition_at") if isinstance(selected_thesis, dict) else None,
                        "scale_out_count": selected_thesis.get("scale_out_count") if isinstance(selected_thesis, dict) else None,
                        "scale_out_stage_total": selected_thesis.get("scale_out_stage_total") if isinstance(selected_thesis, dict) else None,
                        "remaining_quantity": selected_thesis.get("remaining_quantity") if isinstance(selected_thesis, dict) else None,
                        "close_available": close_available,
                    }
                )

            for trade in snapshot.closed_trades:
                if cleared_before is not None and trade.closed_at <= cleared_before:
                    continue
                total_realized += trade.realized_pnl
                trade_day_nz = trade.closed_at.astimezone(_NZ).date()
                if trade_day_nz == today_nz:
                    today_realized += trade.realized_pnl
                elif trade_day_nz == yesterday_nz:
                    yesterday_realized += trade.realized_pnl
                selected_thesis = (
                    self._operator_state_service.find_closed_trade_thesis(
                        market,
                        trade_id=trade.trade_id,
                        symbol=trade.symbol,
                        opened_at=trade.opened_at,
                        closed_at=trade.closed_at,
                    )
                    if self._operator_state_service is not None
                    else None
                )
                if selected_thesis is None and self._thesis_provider is not None:
                    selected_thesis = self._thesis_provider(market, trade.symbol)
                closed_trades.append(_closed_trade_payload(trade, selected_thesis))

        open_positions.sort(key=lambda item: str(item["updated_at"]), reverse=True)
        closed_trades.sort(key=lambda item: str(item["closed_at"]), reverse=True)
        return {
            "generated_at": utc_now().isoformat(),
            "totals": {
                "open_position_count": len(open_positions),
                "closed_trade_count": len(closed_trades),
                "total_unrealized_pnl": str(total_unrealized),
                "total_realized_pnl": str(total_realized),
                "today_realized_pnl": str(today_realized),
                "yesterday_realized_pnl": str(yesterday_realized),
            },
            "open_positions": open_positions,
            "closed_trades": closed_trades,
        }

    def clear_closed_trade_history_payload(self) -> dict[str, object]:
        if self._operator_state_service is None:
            raise ValueError("Closed-trade history clearing is not configured.")
        cleared_before = utc_now()
        self._operator_state_service.clear_closed_trade_history(cleared_before=cleared_before)
        visible_closed_trades = self.get_journal_payload().get("closed_trades", [])
        if not isinstance(visible_closed_trades, list):
            raise ValueError("Trade journal payload has an invalid closed_trades payload.")
        visible_rows = len(visible_closed_trades)
        return {
            "cleared": True,
            "cleared_before": cleared_before.isoformat(),
            "remaining_visible_count": visible_rows,
            "message": "Closed-trade history cleared from this table. Underlying trade records were preserved.",
        }

    def backfill_closed_trades_payload(
        self,
        *,
        market: Market | None = None,
        limit: int = 100,
    ) -> dict[str, object]:
        normalized_limit = max(1, min(limit, 500))
        requested_markets = (market,) if market is not None else (Market.STOCKS, Market.CRYPTO, Market.FOREX)
        stored_snapshots = self._portfolio_store.load_all()
        updated_markets: list[BackfilledMarketPayload] = []
        skipped_markets: list[dict[str, str]] = []

        for requested_market in requested_markets:
            worker = self._workers.get(requested_market)
            if worker is None:
                skipped_markets.append(
                    {"market": requested_market.value, "reason": "No worker configured for this market."}
                )
                continue
            if BrokerCapability.TRADE_HISTORY not in worker.discover_capabilities():
                skipped_markets.append(
                    {"market": requested_market.value, "reason": "Broker trade history is not supported for this market yet."}
                )
                continue

            try:
                closed_trades = worker.list_closed_trades(limit=normalized_limit)
                snapshot = worker.reconcile_portfolio()
            except Exception as exc:
                skipped_markets.append({"market": requested_market.value, "reason": str(exc)})
                continue

            existing_snapshot = stored_snapshots.get(requested_market)
            snapshot_to_save = PortfolioSnapshot(
                market=snapshot.market,
                account=snapshot.account,
                positions=snapshot.positions,
                open_orders=snapshot.open_orders,
                fills=snapshot.fills if snapshot.fills else existing_snapshot.fills if existing_snapshot is not None else (),
                closed_trades=closed_trades,
                as_of=snapshot.as_of,
            )
            self._portfolio_store.save(snapshot_to_save)

            merged_snapshot = self._portfolio_store.load_all().get(requested_market, snapshot_to_save)
            stored_snapshots[requested_market] = merged_snapshot
            if self._operator_state_service is not None:
                self._recover_backfilled_trade_theses(requested_market, merged_snapshot)
            updated_markets.append(
                {
                    "market": requested_market.value,
                    "loaded_trade_count": len(closed_trades),
                    "journal_trade_count": len(merged_snapshot.closed_trades),
                }
            )

        loaded_trade_count = sum(item["loaded_trade_count"] for item in updated_markets)
        scope = market.value if market is not None else "supported markets"
        return {
            "backfilled": bool(updated_markets),
            "requested_market": market.value if market is not None else None,
            "loaded_trade_count": loaded_trade_count,
            "updated_markets": updated_markets,
            "skipped_markets": skipped_markets,
            "message": (
                f"Backfilled {loaded_trade_count} broker trade rows for {scope}."
                if updated_markets
                else f"No broker trade history was backfilled for {scope}."
            ),
        }

    def close_position_payload(self, market: Market, symbol: str) -> dict[str, object]:
        normalized_symbol = symbol.strip().upper()
        if not normalized_symbol:
            raise ValueError("symbol is required")

        snapshots = self._portfolio_store.load_all()
        snapshot = snapshots.get(market)
        if snapshot is None:
            raise ValueError(f"No portfolio snapshot available for market {market.value}.")

        position_to_close = None
        remaining_positions = []
        for position in snapshot.positions:
            if position.symbol.upper() == normalized_symbol:
                position_to_close = position
            else:
                remaining_positions.append(position)

        if position_to_close is None:
            raise ValueError(f"No open position for {normalized_symbol} in market {market.value}.")

        worker = self._worker_for(market)
        if BrokerCapability.SUBMIT_ORDER not in worker.discover_capabilities():
            raise ValueError(f"Manual close is not enabled for market {market.value}.")
        close_order = worker.submit_order(
            OrderRequest(
                client_order_id=_manual_close_client_order_id(market, normalized_symbol),
                symbol=position_to_close.symbol,
                side=OrderSide.SELL if position_to_close.quantity >= 0 else OrderSide.BUY,
                quantity=abs(position_to_close.quantity),
                order_type=OrderType.MARKET,
            )
        )
        if close_order.status != OrderStatus.FILLED:
            raise ValueError(
                f"Close order for {position_to_close.symbol} in market {market.value} did not fill."
            )

        updated_snapshot = worker.reconcile_portfolio()
        closed_trade = next(
            (
                trade
                for trade in reversed(updated_snapshot.closed_trades)
                if trade.symbol.upper() == position_to_close.symbol.upper()
            ),
            None,
        )
        if closed_trade is None:
            closed_trade = _synthesized_closed_trade(
                snapshot=updated_snapshot,
                market=market,
                position_symbol=position_to_close.symbol,
                position_quantity=abs(position_to_close.quantity),
                position_entry_price=position_to_close.average_price,
                position_opened_at=position_to_close.updated_at,
                order=close_order,
            )
            updated_snapshot = PortfolioSnapshot(
                market=updated_snapshot.market,
                account=updated_snapshot.account,
                positions=updated_snapshot.positions,
                open_orders=updated_snapshot.open_orders,
                fills=updated_snapshot.fills,
                closed_trades=(*updated_snapshot.closed_trades, closed_trade),
                as_of=updated_snapshot.as_of,
            )
        self._portfolio_store.save(updated_snapshot)
        if self._operator_state_service is not None:
            active_thesis = self._operator_state_service.get_active_trade_thesis(market, position_to_close.symbol)
            if active_thesis is not None:
                self._operator_state_service.archive_closed_trade_thesis(
                    market,
                    closed_trade.trade_id,
                    active_thesis,
                    trade_symbol=closed_trade.symbol,
                    opened_at=closed_trade.opened_at,
                    closed_at=closed_trade.closed_at,
                    state="manual-closed",
                    reason="manual close requested from journal",
                    transitioned_at=closed_trade.closed_at,
                )
                self._operator_state_service.remove_active_trade_thesis(market, position_to_close.symbol)

        return {
            "closed": True,
            "market": market.value,
            "symbol": position_to_close.symbol,
            "order_id": close_order.order_id,
            "trade_id": closed_trade.trade_id,
            "closed_at": closed_trade.closed_at.isoformat(),
            "realized_pnl": str(closed_trade.realized_pnl),
        }

    def _worker_for(self, market: Market) -> MarketWorker:
        try:
            return self._workers[market]
        except KeyError as exc:
            raise ValueError(f"No worker configured for market {market.value}.") from exc

    def _recover_backfilled_trade_theses(self, market: Market, snapshot: PortfolioSnapshot) -> None:
        assert self._operator_state_service is not None
        active_symbols = {position.symbol.upper() for position in snapshot.positions}
        active_theses = self._operator_state_service.list_active_trade_theses(market)
        for trade in sorted(snapshot.closed_trades, key=lambda item: item.closed_at, reverse=True):
            if self._operator_state_service.find_closed_trade_thesis(
                market,
                trade_id=trade.trade_id,
                symbol=trade.symbol,
                opened_at=trade.opened_at,
                closed_at=trade.closed_at,
            ) is not None:
                continue
            symbol = trade.symbol.upper()
            if symbol in active_symbols:
                continue
            active_thesis = active_theses.get(symbol)
            if active_thesis is None:
                continue
            self._operator_state_service.archive_closed_trade_thesis(
                market,
                trade.trade_id,
                active_thesis,
                trade_symbol=trade.symbol,
                opened_at=trade.opened_at,
                closed_at=trade.closed_at,
                state="recovered-closed",
                reason="broker history backfill matched persisted thesis",
                transitioned_at=trade.closed_at,
            )
            self._operator_state_service.remove_active_trade_thesis(market, trade.symbol)
            active_theses.pop(symbol, None)


def _closed_trade_payload(trade: NormalizedTrade, thesis: dict[str, object] | None = None) -> dict[str, object]:
    return {
        "trade_id": trade.trade_id,
        "market": trade.market.value,
        "symbol": trade.symbol,
        "status": "closed",
        "side": trade.side.value,
        "thesis_id": thesis.get("thesis_id") if isinstance(thesis, dict) else None,
        "strategy_id": thesis.get("strategy_id") if isinstance(thesis, dict) else None,
        "thesis_state": thesis.get("lifecycle_state") if isinstance(thesis, dict) else None,
        "thesis_reason": thesis.get("lifecycle_reason") if isinstance(thesis, dict) else None,
        "thesis_transition_at": thesis.get("last_transition_at") if isinstance(thesis, dict) else None,
        "scale_out_count": thesis.get("scale_out_count") if isinstance(thesis, dict) else None,
        "scale_out_stage_total": thesis.get("scale_out_stage_total") if isinstance(thesis, dict) else None,
        "quantity": str(trade.quantity),
        "entry_price": str(trade.entry_price),
        "exit_price": str(trade.exit_price),
        "opened_at": trade.opened_at.isoformat(),
        "closed_at": trade.closed_at.isoformat(),
        "fees": str(trade.fees),
        "realized_pnl": str(trade.realized_pnl),
    }


def _manual_close_client_order_id(market: Market, symbol: str) -> str:
    safe_symbol = symbol.lower().replace("/", "-")
    return f"close-{market.value}-{safe_symbol}-{uuid4().hex[:10]}"


def _synthesized_closed_trade(
    *,
    snapshot: PortfolioSnapshot,
    market: Market,
    position_symbol: str,
    position_quantity: Decimal,
    position_entry_price: Decimal,
    position_opened_at: datetime,
    order,
) -> NormalizedTrade:
    exit_price = order.average_fill_price or position_entry_price
    return NormalizedTrade(
        trade_id=order.order_id or order.client_order_id or uuid4().hex,
        market=market,
        symbol=position_symbol,
        side=OrderSide.BUY if order.side == OrderSide.SELL else OrderSide.SELL,
        quantity=position_quantity,
        entry_price=position_entry_price,
        exit_price=exit_price,
        opened_at=position_opened_at,
        closed_at=snapshot.as_of,
    )