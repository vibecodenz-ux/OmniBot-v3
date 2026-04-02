"""Consolidated release evidence reporting for OmniBot v3."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime


@dataclass(frozen=True, slots=True)
class ReleaseEvidenceArtifact:
    name: str
    passed: bool
    summary: str
    payload: dict[str, object]


@dataclass(frozen=True, slots=True)
class ReleaseEvidenceReport:
    generated_at: str
    passed: bool
    artifact_count: int
    environment: dict[str, object]
    artifacts: tuple[ReleaseEvidenceArtifact, ...]


@dataclass(frozen=True, slots=True)
class ReleaseEvidenceService:
    def build_report(
        self,
        *,
        quality_gate_payload: dict[str, object],
        api_smoke_payload: dict[str, object],
        release_readiness_payload: dict[str, object],
        environment: dict[str, object],
        generated_at: datetime | None = None,
    ) -> ReleaseEvidenceReport:
        artifacts = (
            self._artifact(
                name="quality-gate",
                payload=quality_gate_payload,
                summary=self._quality_gate_summary(quality_gate_payload),
            ),
            self._artifact(
                name="api-smoke",
                payload=api_smoke_payload,
                summary=self._simple_summary(api_smoke_payload, label="checks"),
            ),
            self._artifact(
                name="release-readiness",
                payload=release_readiness_payload,
                summary=self._simple_summary(release_readiness_payload, label="checks"),
            ),
        )
        timestamp = generated_at or datetime.now(UTC)
        return ReleaseEvidenceReport(
            generated_at=timestamp.isoformat(),
            passed=all(artifact.passed for artifact in artifacts),
            artifact_count=len(artifacts),
            environment=environment,
            artifacts=artifacts,
        )

    def report_to_dict(self, report: ReleaseEvidenceReport) -> dict[str, object]:
        return {
            "generated_at": report.generated_at,
            "passed": report.passed,
            "artifact_count": report.artifact_count,
            "environment": report.environment,
            "artifacts": [asdict(artifact) for artifact in report.artifacts],
        }

    def _artifact(
        self,
        *,
        name: str,
        payload: dict[str, object],
        summary: str,
    ) -> ReleaseEvidenceArtifact:
        passed = bool(payload.get("passed") is True)
        return ReleaseEvidenceArtifact(
            name=name,
            passed=passed,
            summary=summary,
            payload=payload,
        )

    def _quality_gate_summary(self, payload: dict[str, object]) -> str:
        check_count = payload.get("check_count")
        checks = payload.get("checks")
        if not isinstance(checks, list):
            return f"passed={payload.get('passed')} checks={check_count}"
        failed_checks = [
            str(check.get("name"))
            for check in checks
            if isinstance(check, dict) and check.get("passed") is not True
        ]
        if failed_checks:
            return (
                f"passed={payload.get('passed')} checks={check_count} "
                f"failed={', '.join(failed_checks)}"
            )
        return f"passed={payload.get('passed')} checks={check_count}"

    def _simple_summary(self, payload: dict[str, object], *, label: str) -> str:
        return f"passed={payload.get('passed')} {label}={payload.get('check_count')}"
