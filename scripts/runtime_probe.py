"""Run OmniBot runtime health or readiness probes with supervisor-friendly exit codes."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run OmniBot runtime supervision probes.")
    parser.add_argument("--mode", choices=("health", "readiness"), default="health")
    parser.add_argument("--format", choices=("json", "text"), default="json")
    parser.add_argument("--connect-markets", action="store_true")
    parser.add_argument("--validate-workers", action="store_true")
    parser.add_argument("--reconcile-workers", action="store_true")
    return parser.parse_args()


def main() -> int:
    from omnibot_v3.domain import ConnectMarket, Market
    from omnibot_v3.services import (
        RuntimeProbeService,
        build_default_market_workers,
        build_default_orchestrator,
    )

    args = _parse_args()
    orchestrator = build_default_orchestrator()
    workers = build_default_market_workers()

    if args.validate_workers:
        for worker in workers.values():
            worker.validate_configuration()
    if args.reconcile_workers:
        for worker in workers.values():
            worker.reconcile_portfolio()
    if args.connect_markets:
        for market in (Market.STOCKS, Market.CRYPTO, Market.FOREX):
            orchestrator.handle(ConnectMarket(market=market))

    service = RuntimeProbeService()
    exit_code, payload = service.probe(orchestrator=orchestrator, workers=workers, mode=args.mode)

    if args.format == "json":
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"mode={payload['mode']} exit_code={payload['exit_code']} state={payload['state']} ready={payload['ready']}")
        for report in payload["market_reports"]:
            print(
                f"market={report['market']} state={report['state']} ready={report['ready']} reason={report['reason']}"
            )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
