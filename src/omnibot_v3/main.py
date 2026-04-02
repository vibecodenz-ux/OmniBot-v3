"""CLI entrypoint for OmniBot v3."""

from omnibot_v3 import __version__
from omnibot_v3.domain import Market, load_config
from omnibot_v3.services import build_default_orchestrator


def main() -> int:
    config = load_config()
    orchestrator = build_default_orchestrator()
    print(f"OmniBot v3 {__version__}")
    print(
        "Runtime scaffold ready with explicit per-market state orchestration "
        f"in {config.environment} mode."
    )
    for market in (Market.STOCKS, Market.CRYPTO, Market.FOREX):
        snapshot = orchestrator.snapshot(market)
        print(f"- {market}: {snapshot.state}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())