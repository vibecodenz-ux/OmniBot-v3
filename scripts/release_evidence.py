"""Generate a consolidated local release evidence artifact for OmniBot v3."""

from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate OmniBot local release evidence.")
    parser.add_argument("--format", choices=("json", "text"), default="json")
    parser.add_argument("--output-file")
    parser.add_argument("--coverage-xml", default="coverage.xml")
    return parser.parse_args()


def _run_quality_gate(coverage_xml: str) -> dict[str, object]:
    result = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "quality_gate.py"),
            "--format",
            "json",
            "--coverage-xml",
            coverage_xml,
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    output = result.stdout.strip() or result.stderr.strip()
    if not output:
        raise RuntimeError("quality_gate.py produced no output")
    return json.loads(output)


def _text_report(payload: dict[str, object]) -> str:
    lines = [
        f"passed={payload['passed']} artifacts={payload['artifact_count']}",
    ]
    environment = payload.get("environment", {})
    if isinstance(environment, dict):
        lines.append(
            f"python={environment.get('python_version')} platform={environment.get('platform')}"
        )
    artifacts = payload.get("artifacts", [])
    if isinstance(artifacts, list):
        for artifact in artifacts:
            if isinstance(artifact, dict):
                lines.append(
                    f"- {artifact.get('name')}: {artifact.get('summary')}"
                )
    return "\n".join(lines)


def main() -> int:
    from omnibot_v3.api import create_app
    from omnibot_v3.services import ApiSmokeService, ReleaseEvidenceService, ReleaseReadinessService

    args = _parse_args()

    quality_gate_payload = _run_quality_gate(args.coverage_xml)
    with TestClient(create_app()) as client:
        api_smoke_payload = ApiSmokeService().report_to_dict(ApiSmokeService().run(client))
    release_readiness_payload = ReleaseReadinessService().report_to_dict(
        ReleaseReadinessService().run()
    )
    environment = {
        "platform": platform.platform(),
        "python_version": platform.python_version(),
        "coverage_xml": args.coverage_xml,
    }
    payload = ReleaseEvidenceService().report_to_dict(
        ReleaseEvidenceService().build_report(
            quality_gate_payload=quality_gate_payload,
            api_smoke_payload=api_smoke_payload,
            release_readiness_payload=release_readiness_payload,
            environment=environment,
        )
    )

    rendered = (
        json.dumps(payload, indent=2, sort_keys=True)
        if args.format == "json"
        else _text_report(payload)
    )
    if args.output_file:
        Path(args.output_file).write_text(rendered, encoding="utf-8")
    else:
        print(rendered)
    return 0 if payload["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())