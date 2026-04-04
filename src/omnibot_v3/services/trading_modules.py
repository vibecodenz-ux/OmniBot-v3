"""Trading-module metadata and in-memory operator selections for the dashboard."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from omnibot_v3.domain import BrokerHealth, BrokerHealthStatus
from omnibot_v3.domain.runtime import Market
from omnibot_v3.services.market_catalog import (
    normalize_profile_id,
)
from omnibot_v3.services.market_worker import MarketWorker
from omnibot_v3.services.operator_state import OperatorStateService


AUTONOMOUS_STRATEGY_MODE = "auto-rotate"


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
                profile_id = normalize_profile_id(market, selection.profile_id)
                try:
                    _option_by_id(definition.profiles, profile_id)
                except ValueError:
                    continue
                self.selections[market] = TradingModuleSelection(
                    profile_id=profile_id,
                    updated_at=selection.updated_at,
                )

        for market, definition in self.definitions.items():
            self.selections.setdefault(
                market,
                TradingModuleSelection(
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
            selected_profile = _option_by_id(definition.profiles, selection.profile_id)
            modules.append(
                {
                    "market": market.value,
                    "label": definition.label,
                    "descriptor": definition.descriptor,
                    "symbols": list(worker.settings.symbols),
                    "symbols_tooltip": _symbols_tooltip(worker.settings.symbols),
                    "autonomy_mode": AUTONOMOUS_STRATEGY_MODE,
                    "selected_profile_id": selection.profile_id,
                    "strategy_family_summary": _strategy_family_summary(definition),
                    "strategy_family_note": _strategy_family_note(definition),
                    "profile_summary": selected_profile.summary,
                    "profile_note": selected_profile.note,
                    "module_scope": _module_scope_label(market),
                    "module_notes": _module_notes(market),
                    "active_guardrails": _active_guardrails(market),
                    **_module_status_payload(worker, health),
                    **(self.activity_provider(market) if self.activity_provider is not None else {}),
                    "updated_at": selection.updated_at.isoformat(),
                    "strategy_families": [_option_to_payload(option) for option in definition.strategies],
                    "profiles": [_option_to_payload(option) for option in definition.profiles],
                }
            )
        return {"generated_at": generated_at, "modules": modules}

    def current_selection(self, market: Market) -> tuple[str, str]:
        definition = self._definition_for(market)
        selection = self.selections[market]
        return definition.default_strategy_id, selection.profile_id

    def update_selection_payload(
        self,
        market: Market,
        profile_id: str | None,
    ) -> dict[str, object]:
        definition = self._definition_for(market)
        current = self.selections[market]
        next_profile_id = normalize_profile_id(market, profile_id or current.profile_id)

        _option_by_id(definition.profiles, next_profile_id)

        self.selections[market] = TradingModuleSelection(
            profile_id=next_profile_id,
        )
        if self.operator_state_service is not None:
            updated_selection = self.selections[market]
            self.operator_state_service.update_trading_module_selection(
                market,
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


def _strategy_family_summary(definition: TradingModuleDefinition) -> str:
    family_names = ", ".join(option.name for option in definition.strategies)
    return f"Scanner rotates across {family_names} setups and only acts when the live thesis clears the market and portfolio brakes."


def _strategy_family_note(definition: TradingModuleDefinition) -> str:
    recommended = next((option.name for option in definition.strategies if option.recommended), definition.strategies[0].name)
    return f"No market-level strategy is pinned here. The engine keeps every supported family available and uses {recommended} only as the passive fallback when a position overlay needs a neutral reference."


def _build_default_definitions() -> dict[Market, TradingModuleDefinition]:
    return {
        Market.STOCKS: TradingModuleDefinition(
            market=Market.STOCKS,
            label="Alpaca",
            descriptor="Auto engine for the configured US stock watchlist.",
            strategies=(
                ModuleOption(
                    option_id="momentum",
                    name="Trend Follow",
                    summary="Buys strength and exits when the move stalls, the hard risk rules trigger, or the profit target is met.",
                    note="Best for clean directional sessions. Live exits now also use a hard stop, profit target above estimated costs, and a max hold timer.",
                    recommended=True,
                ),
                ModuleOption(
                    option_id="breakout",
                    name="Range Breakout",
                    summary="Waits for price to escape a recent range, then manages the trade with the selected profile's exit rules.",
                    note="Best when the market opens quietly and then expands with conviction.",
                ),
                ModuleOption(
                    option_id="mean_reversion",
                    name="Fade Reversal",
                    summary="Looks for stretched price moves to snap back toward fair value, with the same hard exits layered on top.",
                    note="Use when the market is choppy and rotating rather than trending cleanly.",
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
                    name="Balanced Autopilot",
                    summary="Medium position size, medium signal strictness, about 0.6% net profit target after estimated costs, 1.2% stop, 6h max hold.",
                    note="Best default when you want the bot to trade on its own without holding stale positions all day.",
                    recommended=True,
                ),
                ModuleOption(
                    option_id="conservative",
                    name="Capital Protection",
                    summary="Smaller size with stricter entries, about 0.4% net profit target after estimated costs, 0.9% stop, 8h max hold.",
                    note="Use this when you want fewer trades and tighter downside control.",
                ),
                ModuleOption(
                    option_id="aggressive",
                    name="Growth Focus",
                    summary="Larger size and looser entries, about 0.9% net profit target after estimated costs, 1.8% stop, 4h max hold.",
                    note="Takes more risk and expects cleaner trends. Use only when the market is liquid and stable.",
                ),
                ModuleOption(
                    option_id="hft",
                    name="Fast Probe",
                    summary="Very fast entries with small size, about 0.3% net profit target after estimated costs, 0.6% stop, 75m max hold.",
                    note="Useful for short demo probes, but it can churn more and is not the best default for steady automation.",
                ),
            ),
        ),
        Market.CRYPTO: TradingModuleDefinition(
            market=Market.CRYPTO,
            label="Binance Futures Demo",
            descriptor="Auto engine for the configured Binance Futures demo pairs.",
            strategies=(
                ModuleOption(
                    option_id="breakout",
                    name="Range Breakout",
                    summary="Waits for crypto to break out of compression, then hands the trade to the profile's hard exit rules.",
                    note="Good default for crypto because it avoids some chop and now has explicit stop, profit, and hold limits.",
                    recommended=True,
                ),
                ModuleOption(
                    option_id="momentum",
                    name="Trend Follow",
                    summary="Joins one-sided crypto moves early and exits with both signal logic and hard risk rules.",
                    note="Works best when majors are trending instead of whipping around.",
                ),
                ModuleOption(
                    option_id="ml_ensemble",
                    name="Multi-Signal Consensus",
                    summary="Requires several signal families to agree before opening a trade, then uses the same hard exits as the other live modes.",
                    note="Usually trades less often, but is easier to trust because it waits for agreement.",
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
                    name="Balanced Autopilot",
                    summary="Medium size, about 0.6% net profit target after estimated crypto costs, 1.2% stop, 6h max hold.",
                    note="Best default for live crypto demo trading because it balances trade frequency with faster exits.",
                    recommended=True,
                ),
                ModuleOption(
                    option_id="conservative",
                    name="Capital Protection",
                    summary="Smaller size, about 0.4% net profit target after estimated crypto costs, 0.9% stop, 8h max hold.",
                    note="Best when exchange conditions feel messy and you want to slow the bot down.",
                ),
                ModuleOption(
                    option_id="aggressive",
                    name="Growth Focus",
                    summary="Larger size, about 0.9% net profit target after estimated crypto costs, 1.8% stop, 4h max hold.",
                    note="More willing to chase momentum and recycle quickly, but it will take larger swings.",
                ),
                ModuleOption(
                    option_id="hft",
                    name="Fast Probe",
                    summary="Small size, very fast entries, about 0.3% net profit target after estimated crypto costs, 0.6% stop, 75m max hold.",
                    note="Useful for proving the full order loop quickly, but it is more sensitive to noise and fees.",
                ),
            ),
        ),
        Market.FOREX: TradingModuleDefinition(
            market=Market.FOREX,
            label="IG Forex AU",
            descriptor="Auto engine for the configured forex majors on your demo account.",
            strategies=(
                ModuleOption(
                    option_id="mean_reversion",
                    name="Fade Reversal",
                    summary="Looks for majors to snap back after a stretch and then manages the trade with hard exits.",
                    note="Best forex starting point when the majors are ranging instead of breaking out.",
                    recommended=True,
                ),
                ModuleOption(
                    option_id="momentum",
                    name="Trend Follow",
                    summary="Follows stronger directional FX moves and exits with a stop, profit target, or hold timer.",
                    note="Use this when the majors are clearly trending after macro news or session breaks.",
                ),
                ModuleOption(
                    option_id="breakout",
                    name="Range Breakout",
                    summary="Waits for London or New York range expansion, then manages the position with the selected profile's exits.",
                    note="Best when the pairs have been quiet and are starting to expand.",
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
                    name="Balanced Autopilot",
                    summary="Medium size, about 0.6% net profit target after estimated costs, 1.2% stop, 6h max hold.",
                    note="Best default for FX automation once the market is open and liquid.",
                    recommended=True,
                ),
                ModuleOption(
                    option_id="conservative",
                    name="Capital Protection",
                    summary="Smaller size, about 0.4% net profit target after estimated costs, 0.9% stop, 8h max hold.",
                    note="Best when headlines are noisy or you want the bot to trade less often.",
                ),
                ModuleOption(
                    option_id="aggressive",
                    name="Growth Focus",
                    summary="Larger size, about 0.9% net profit target after estimated costs, 1.8% stop, 4h max hold.",
                    note="Use only when spreads are healthy and the majors are moving cleanly.",
                ),
                ModuleOption(
                    option_id="hft",
                    name="Fast Probe",
                    summary="Small size, fast entries, about 0.3% net profit target after estimated costs, 0.6% stop, 75m max hold.",
                    note="Useful for quick demo validation, but not the best default for steadier live-style behavior.",
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
        return "Configured US stock watchlist"
    if market == Market.CRYPTO:
        return "Configured Binance Futures demo pairs"
    return "Configured forex majors"


def _module_notes(market: Market) -> list[str]:
    if market == Market.STOCKS:
        return [
            "The engine only scans the symbols configured for this market, not the full exchange.",
            "Advanced overrides tune the legacy entry model and risk posture without changing broker connectivity or safety rails.",
        ]
    if market == Market.CRYPTO:
        return [
            "The engine only scans the configured Binance Futures demo pairs available to this workspace.",
            "Advanced overrides tune entry and risk behavior while the shared crypto execution rails stay in place.",
        ]
    return [
        "The engine only scans the configured forex majors for this market, not every product exposed by IG.",
        "Advanced overrides tune the legacy setup selection while the same FX guardrails stay active underneath.",
    ]


def _active_guardrails(market: Market) -> list[str]:
    guardrails = [
        "Every live position keeps a hard stop active.",
        "Profit capture waits until estimated trading costs are covered.",
        "A max-hold timer forces stale positions out of the book.",
    ]
    if market == Market.STOCKS:
        guardrails.append("Order flow stays limited to the configured stock watchlist.")
    elif market == Market.CRYPTO:
        guardrails.append("Order flow stays limited to the configured Binance Futures demo pairs.")
    else:
        guardrails.append("Order flow stays limited to the configured forex majors for this market.")
    return guardrails


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