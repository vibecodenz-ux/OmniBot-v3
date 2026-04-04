"""Run local quality gates that mirror the core CI checks."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


@dataclass(frozen=True, slots=True)
class QualityGateCheck:
    name: str
    command: tuple[str, ...]
    passed: bool
    output: str


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run local OmniBot quality gates.")
    parser.add_argument("--format", choices=("json", "text"), default="json")
    parser.add_argument("--coverage-xml")
    return parser.parse_args()


def _run_check(name: str, command: tuple[str, ...]) -> QualityGateCheck:
    result = subprocess.run(
        list(command),
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    combined_output = "\n".join(part for part in (result.stdout.strip(), result.stderr.strip()) if part)
    return QualityGateCheck(
        name=name,
        command=command,
        passed=result.returncode == 0,
        output=combined_output,
    )


def _report_payload(checks: tuple[QualityGateCheck, ...]) -> dict[str, object]:
    return {
        "passed": all(check.passed for check in checks),
        "check_count": len(checks),
        "checks": [
            {
                "name": check.name,
                "command": list(check.command),
                "passed": check.passed,
                "output": check.output,
            }
            for check in checks
        ],
    }


def _text_report(payload: dict[str, object]) -> str:
    lines = [
        f"passed={payload['passed']} checks={payload['check_count']}",
    ]
    checks = payload.get("checks", [])
    if isinstance(checks, list):
        for check in checks:
            if isinstance(check, dict):
                lines.append(f"name={check.get('name')} passed={check.get('passed')}")
    return "\n".join(lines)


def main() -> int:
    args = _parse_args()
    coverage_command = [
        sys.executable,
        "-m",
        "pytest",
        "--cov=src/omnibot_v3",
        "--cov-report=term-missing",
    ]
    if args.coverage_xml:
        coverage_command.append(f"--cov-report=xml:{args.coverage_xml}")

    checks = (
        _run_check("ruff-check", (sys.executable, "-m", "ruff", "check", ".")),
        _run_check("mypy", (sys.executable, "-m", "mypy")),
        _run_check("pytest-coverage", tuple(coverage_command)),
    )
    payload = _report_payload(checks)

    if args.format == "json":
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(_text_report(payload))
        for check in checks:
            if check.output:
                print(f"[{check.name}]\n{check.output}")
    return 0 if payload["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())