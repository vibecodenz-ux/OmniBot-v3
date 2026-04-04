"""Run OmniBot release-readiness validation checks."""

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
    parser = argparse.ArgumentParser(description="Run OmniBot release-readiness validation.")
    parser.add_argument("--format", choices=("json", "text"), default="json")
    return parser.parse_args()


def main() -> int:
    from omnibot_v3.services import ReleaseReadinessService

    args = _parse_args()
    service = ReleaseReadinessService()
    report = service.run()

    if args.format == "json":
        print(json.dumps(service.report_to_dict(report), indent=2, sort_keys=True))
    else:
        print(f"passed={report.passed} checks={report.check_count} checked_at={report.checked_at}")
        for check in report.checks:
            print(f"name={check.name} passed={check.passed} detail={check.detail}")
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())