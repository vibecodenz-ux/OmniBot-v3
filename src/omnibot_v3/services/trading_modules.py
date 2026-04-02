"""Trading-module metadata and in-memory operator selections for the dashboard."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from omnibot_v3.domain import BrokerHealth, BrokerHealthStatus
from omnibot_v3.domain.runtime import Market
from omnibot_v3.services.market_catalog import (
    normalize_profile_id,
    normalize_strategy_id,
)
from omnibot_v3.services.market_worker import MarketWorker
from omnibot_v3.services.operator_state import OperatorStateService


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class ModuleOption:
    option_id: str
    name: str
    summary: str
    note: str
    recommended: bool = False


@dataclass(frozen=True, slots=True)
class TradingModuleSelection:
    strategy_id: str
    profile_id: str
    updated_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True, slots=True)
class TradingModuleDefinition:
    market: Market
    label: str
    descriptor: str
    strategies: tuple[ModuleOption, ...]
    profiles: tuple[ModuleOption, ...]

    @property
    def default_strategy_id(self) -> str:
        return self.strategies[0].option_id

    @property
    def default_profile_id(self) -> str:
        return self.profiles[0].option_id


@dataclass(slots=True)
class TradingModuleService:
    workers: dict[Market, MarketWorker]
    definitions: dict[Market, TradingModuleDefinition] = field(
        default_factory=lambda: _build_default_definitions()
    )
    selections: dict[Market, TradingModuleSelection] = field(default_factory=dict)
    operator_state_service: OperatorStateService | None = None
    activity_provider: Callable[[Market], dict[str, object]] | None = None
    health_provider: Callable[[Market], BrokerHealth] | None = None

    def __post_init__(self) -> None:
        if self.operator_state_service is not None:
            for market, selection in self.operator_state_service.list_trading_module_selections().items():
                if market not in self.definitions:
                    continue
                definition = self.definitions[market]
                strategy_id = normalize_strategy_id(market, selection.strategy_id)
                profile_id = normalize_profile_id(market, selection.profile_id)
                try:
                    _option_by_id(definition.strategies, strategy_id)
                    _option_by_id(definition.profiles, profile_id)
                except ValueError:
                    continue
                self.selections[market] = TradingModuleSelection(
                    strategy_id=strategy_id,
                    profile_id=profile_id,
                    updated_at=selection.updated_at,
                )

        for market, definition in self.definitions.items():
            self.selections.setdefault(
                market,
                TradingModuleSelection(
                    strategy_id=definition.default_strategy_id,
                    profile_id=definition.default_profile_id,
                ),
            )

    def list_modules_payload(self) -> dict[str, object]:
        generated_at = utc_now().isoformat()
        modules: list[dict[str, object]] = []
        for market in (Market.STOCKS, Market.CRYPTO, Market.FOREX):
            definition = self._definition_for(market)
            selection = self.selections[market]
            worker = self._worker_for(market)
            health = self.health_provider(market) if self.health_provider is not None else worker.health_check()
            selected_strategy = _option_by_id(definition.strategies, selection.strategy_id)
            selected_profile = _option_by_id(definition.profiles, selection.profile_id)
            modules.append(
                {
                    "market": market.value,
                    "label": definition.label,
                    "descriptor": definition.descriptor,
                    "symbols": list(worker.settings.symbols),
                    "symbols_tooltip": _symbols_tooltip(worker.settings.symbols),
                    "selected_strategy_id": selection.strategy_id,
                    "selected_profile_id": selection.profile_id,
                    "strategy_summary": selected_strategy.summary,
                    "strategy_note": selected_strategy.note,
                    "profile_summary": selected_profile.summary,
                    "profile_note": selected_profile.note,
                    "module_scope": _module_scope_label(market),
                    "module_notes": _module_notes(market),
                    **_module_status_payload(worker, health),
                    **(self.activity_provider(market) if self.activity_provider is not None else {}),
                    "updated_at": selection.updated_at.isoformat(),
                    "strategies": [_option_to_payload(option) for option in definition.strategies],
                    "profiles": [_option_to_payload(option) for option in definition.profiles],
                }
            )
        return {"generated_at": generated_at, "modules": modules}

    def current_selection(self, market: Market) -> tuple[str, str]:
        selection = self.selections[market]
        return selection.strategy_id, selection.profile_id

    def update_selection_payload(
        self,
        market: Market,
        strategy_id: str | None,
        profile_id: str | None,
    ) -> dict[str, object]:
        definition = self._definition_for(market)
        current = self.selections[market]
        next_strategy_id = normalize_strategy_id(market, strategy_id or current.strategy_id)
        next_profile_id = normalize_profile_id(market, profile_id or current.profile_id)

        _option_by_id(definition.strategies, next_strategy_id)
        _option_by_id(definition.profiles, next_profile_id)

        self.selections[market] = TradingModuleSelection(
            strategy_id=next_strategy_id,
            profile_id=next_profile_id,
        )
        if self.operator_state_service is not None:
            updated_selection = self.selections[market]
            self.operator_state_service.update_trading_module_selection(
                market,
                strategy_id=updated_selection.strategy_id,
                profile_id=updated_selection.profile_id,
                updated_at=updated_selection.updated_at,
            )
        payload = self.list_modules_payload()
        modules = payload["modules"]
        if not isinstance(modules, list):
            raise ValueError("Trading module payload is malformed.")
        for module in modules:
            if module["market"] == market.value:
                return module
        raise ValueError(f"No trading module payload found for market {market.value}.")

    def _definition_for(self, market: Market) -> TradingModuleDefinition:
        try:
            return self.definitions[market]
        except KeyError as exc:
            raise ValueError(f"No trading module definition for market {market.value}.") from exc

    def _worker_for(self, market: Market) -> MarketWorker:
        try:
            return self.workers[market]
        except KeyError as exc:
            raise ValueError(f"No worker configured for market {market.value}.") from exc


def _option_to_payload(option: ModuleOption) -> dict[str, object]:
    return {
        "id": option.option_id,
        "name": option.name,
        "summary": option.summary,
        "note": option.note,
        "recommended": option.recommended,
    }


def _option_by_id(options: tuple[ModuleOption, ...], option_id: str) -> ModuleOption:
    for option in options:
        if option.option_id == option_id:
            return option
    raise ValueError(f"Unknown module option '{option_id}'.")


def _build_default_definitions() -> dict[Market, TradingModuleDefinition]:
    return {
        Market.STOCKS: TradingModuleDefinition(
            market=Market.STOCKS,
            label="Alpaca",
            descriptor="v2.7-aligned US equities automation.",
            strategies=(
                ModuleOption(
                    option_id="momentum",
                    name="Momentum Trend",
                    summary="EMA and RSI-style trend following for the liquid ETF and large-cap basket.",
                    note="This was the stock default in v2.7 and maps best to the current live scanner.",
                    recommended=True,
                ),
                ModuleOption(
                    option_id="breakout",
                    name="Breakout Expansion",
                    summary="Range expansion and continuation logic for decisive sessions.",
                    note="Best when the open resolves cleanly and leaders are taking out recent highs.",
                ),
                ModuleOption(
                    option_id="mean_reversion",
                    name="Mean Reversion",
                    summary="Fade stretched price action back toward local value during quieter tape.",
                    note="Use when the market is rotational instead of strongly trending.",
                ),
                ModuleOption(
                    option_id="test_drive",
                    name="Test Drive",
                    summary="Demo-only permissive mode that will trade on minimal confirmation to prove the order path works.",
                    note="Use only on sandbox or demo accounts when you want to force end-to-end signal and order activity.",
                ),
            ),
            profiles=(
                ModuleOption(
                    option_id="moderate",
                    name="Moderate",
                    summary="Balanced cadence and risk for normal autonomous operation.",
                    note="This was the default stock profile in v2.7 and is the best default here too.",
                    recommended=True,
                ),
                ModuleOption(
                    option_id="conservative",
                    name="Conservative",
                    summary="Lower risk with slower acceptance and stricter signal confirmation.",
                    note="Useful while validating a broker setup or uncertain tape.",
                ),
                ModuleOption(
                    option_id="aggressive",
                    name="Aggressive",
                    summary="Looser thresholds, larger sizing, and faster re-entry when conditions stay clean.",
                    note="Use only when the feed and execution path are both stable.",
                ),
                ModuleOption(
                    option_id="hft",
                    name="HFT",
                    summary="Fastest cadence profile with smaller size and the loosest thresholds.",
                    note="Still supported, but not recommended for this one-host build.",
                ),
            ),
        ),
        Market.CRYPTO: TradingModuleDefinition(
            market=Market.CRYPTO,
            label="Binance Futures Demo",
            descriptor="v2.7-aligned USD-M futures demo automation.",
            strategies=(
                ModuleOption(
                    option_id="breakout",
                    name="Breakout Expansion",
                    summary="Default v2.7 crypto strategy for compression breaks and continuation.",
                    note="Best fit for 24/7 crypto volatility and the current live scanner.",
                    recommended=True,
                ),
                ModuleOption(
                    option_id="momentum",
                    name="Momentum Trend",
                    summary="Follow directional continuation when the tape remains one-sided.",
                    note="Better for persistent BTC and ETH trend phases than choppy rotation.",
                ),
                ModuleOption(
                    option_id="ml_ensemble",
                    name="Ensemble Consensus",
                    summary="Requires agreement across multiple signal families before acting.",
                    note="The strongest v2.7-only addition because it is materially different from simple one-rule entries.",
                ),
                ModuleOption(
                    option_id="test_drive",
                    name="Test Drive",
                    summary="Demo-only permissive mode that will trade on minimal confirmation to prove the order path works.",
                    note="Use only on sandbox or demo accounts when you want to force end-to-end signal and order activity.",
                ),
            ),
            profiles=(
                ModuleOption(
                    option_id="moderate",
                    name="Moderate",
                    summary="Balanced cadence and risk for normal automated crypto operation.",
                    note="This matches the v2.7 crypto default.",
                    recommended=True,
                ),
                ModuleOption(
                    option_id="conservative",
                    name="Conservative",
                    summary="Lower size with stricter confirmation during unstable exchange conditions.",
                    note="Good fallback when spread quality or responsiveness degrades.",
                ),
                ModuleOption(
                    option_id="aggressive",
                    name="Aggressive",
                    summary="Higher participation with larger notional and faster re-entry.",
                    note="Closest to the old beta-seeking behavior, but aligned to v2.7 names.",
                ),
                ModuleOption(
                    option_id="hft",
                    name="HFT",
                    summary="Fastest cadence profile with smaller sizing and loose confirmation.",
                    note="Supported for continuity with v2.7, but still not recommended by default.",
                ),
            ),
        ),
        Market.FOREX: TradingModuleDefinition(
            market=Market.FOREX,
            label="IG Forex AU",
            descriptor="v2.7-aligned forex demo execution.",
            strategies=(
                ModuleOption(
                    option_id="mean_reversion",
                    name="Mean Reversion",
                    summary="Default v2.7 forex strategy for liquid majors and contained session ranges.",
                    note="Best starting point for the current IG demo basket.",
                    recommended=True,
                ),
                ModuleOption(
                    option_id="momentum",
                    name="Momentum Trend",
                    summary="Directional continuation for stronger macro-driven sessions.",
                    note="Switch to this when majors are cleanly trending instead of reverting.",
                ),
                ModuleOption(
                    option_id="breakout",
                    name="Breakout Expansion",
                    summary="Range escape logic for London or New York driven volatility expansion.",
                    note="Use when the majors are resolving from compression.",
                ),
                ModuleOption(
                    option_id="test_drive",
                    name="Test Drive",
                    summary="Demo-only permissive mode that will trade on minimal confirmation to prove the order path works.",
                    note="Use only on sandbox or demo accounts when you want to force end-to-end signal and order activity.",
                ),
            ),
            profiles=(
                ModuleOption(
                    option_id="moderate",
                    name="Moderate",
                    summary="Balanced cadence and risk for normal FX automation.",
                    note="This matches the v2.7 forex default.",
                    recommended=True,
                ),
                ModuleOption(
                    option_id="conservative",
                    name="Conservative",
                    summary="Lower size and stricter confirmation for quieter or headline-heavy sessions.",
                    note="Useful around macro releases or while validating IG setup.",
                ),
                ModuleOption(
                    option_id="aggressive",
                    name="Aggressive",
                    summary="Larger notional and faster re-entry when majors are directional and liquid.",
                    note="Closest live-demo equivalent to the old carry-bias posture.",
                ),
                ModuleOption(
                    option_id="hft",
                    name="HFT",
                    summary="Fastest cadence profile with small sizing and looser confirmation.",
                    note="Supported for v2.7 parity, but not recommended as the default profile.",
                ),
            ),
        ),
    }


def _symbols_tooltip(symbols: tuple[str, ...]) -> str:
    if not symbols:
        return "No instruments configured."
    return ", ".join(symbols)


def _module_scope_label(market: Market) -> str:
    if market == Market.STOCKS:
        return "US stock module"
    if market == Market.CRYPTO:
        return "USD-M futures demo module"
    return "Forex demo module"


def _module_notes(market: Market) -> list[str]:
    if market == Market.STOCKS:
        return [
            "This module trades stocks only.",
            "It works from the configured symbol basket, not the full exchange universe.",
        ]
    if market == Market.CRYPTO:
        return [
            "This module trades crypto only.",
            "Supported instruments depend on the configured Binance Futures demo environment.",
        ]
    return [
        "This module trades forex only.",
        "It is limited to the configured FX pair basket, not the full IG product catalogue.",
    ]


def _module_status_payload(worker: MarketWorker, health: BrokerHealth) -> dict[str, object]:
    validation = worker.validate_configuration()
    configured = validation.valid
    connected = configured and health.status == BrokerHealthStatus.HEALTHY

    if connected:
        state = "connected"
        level = "success"
        message = health.message or "Broker connection is healthy."
    elif configured and health.status == BrokerHealthStatus.DEGRADED:
        state = "broker-api-failure"
        level = "warning"
        message = health.message or "Broker API connection is degraded."
    elif configured:
        state = "broker-api-failure"
        level = "error"
        message = _friendly_broker_status_message(worker, health.message) or "Broker API connection failed."
    else:
        state = "awaiting-credentials"
        level = "warning"
        message = "; ".join(validation.errors) or "Broker credentials are still incomplete."

    return {
        "status_state": state,
        "status_level": level,
        "status_message": message,
        "status_details": _broker_status_details(worker, validation.errors if not configured else health.message),
        "credentials_state": "configured" if configured else "missing-credentials",
        "connection_state": "connected" if connected else ("broker-api-failure" if configured else "awaiting-credentials"),
    }


def _friendly_broker_status_message(worker: MarketWorker, raw_message: str | None) -> str | None:
    message = (raw_message or "").strip()
    if not message:
        return None
    adapter_name = worker.adapter.metadata().adapter_name
    if adapter_name == "binance" and "-2015" in message:
        return "Binance rejected the API key. Check the key and secret pair, USD-M Futures permissions, and any IP allowlist on the Binance account. Demo keys must be used against the Binance Futures demo environment."
    if adapter_name == "binance" and _is_binance_timing_message(message):
        return "Binance rejected the request timestamp. The app will retry using Binance server time, but a large local clock drift or unstable connection can still block login."
    return message


def _broker_status_details(worker: MarketWorker, raw_message: str | list[str] | tuple[str, ...] | None) -> list[str]:
    if isinstance(raw_message, (list, tuple)):
        return [str(item) for item in raw_message if str(item).strip()]

    message = (raw_message or "").strip()
    if not message:
        return []

    adapter_name = worker.adapter.metadata().adapter_name
    if adapter_name == "binance" and "-2015" in message:
        return [
            "Verify the API key and secret belong to the same Binance Futures demo account.",
            "Confirm the key has USD-M Futures permission enabled.",
            "If the key uses an IP allowlist, add this machine's public IP.",
            "Use Futures demo keys, not Spot testnet keys or live-account keys.",
        ]
    if adapter_name == "binance" and _is_binance_timing_message(message):
        return [
            "Re-sync the local system clock with Windows time and retry.",
            "The adapter now retries with Binance server time and a larger recvWindow, but severe drift can still fail.",
            "If the problem is intermittent, retry after network latency settles.",
        ]
    return []


def _is_binance_timing_message(message: str) -> bool:
    normalized = message.lower()
    return (
        "timestamp for this request" in normalized
        or "recvwindow" in normalized
        or "server time" in normalized
        or '"code":-1021' in normalized
    )