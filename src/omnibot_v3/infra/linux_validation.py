"""Linux VM validation planning and execution helpers for OmniBot v3."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from omnibot_v3.infra.linux_install import (
    LinuxInstallConfig,
    LinuxInstallPlan,
    LinuxInstallStep,
    build_linux_install_plan,
    build_linux_upgrade_plan,
)


@dataclass(frozen=True, slots=True)
class LinuxValidationPhase:
    name: str
    description: str
    steps: tuple[LinuxInstallStep, ...]


@dataclass(frozen=True, slots=True)
class LinuxValidationPlan:
    distribution: str
    phases: tuple[LinuxValidationPhase, ...]
    manual_checks: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class LinuxValidationStepResult:
    phase_name: str
    step_name: str
    description: str
    command: tuple[str, ...]
    return_code: int
    passed: bool
    stdout: str
    stderr: str


@dataclass(frozen=True, slots=True)
class LinuxValidationReport:
    distribution: str
    started_at: datetime
    completed_at: datetime
    passed: bool
    results: tuple[LinuxValidationStepResult, ...]
    manual_checks: tuple[str, ...]


def build_linux_validation_plan(
    config: LinuxInstallConfig,
    distribution: str = "ubuntu-24.04",
) -> LinuxValidationPlan:
    install_plan = build_linux_install_plan(config)
    upgrade_plan = LinuxInstallPlan(
        steps=tuple(_normalize_validation_step(step) for step in build_linux_upgrade_plan(config).steps)
    )
    phases = (
        LinuxValidationPhase(
            name="install-validation",
            description="run the clean-host install flow on the target Linux VM",
            steps=install_plan.steps,
        ),
        LinuxValidationPhase(
            name="upgrade-validation",
            description="run the post-install upgrade flow on the target Linux VM",
            steps=upgrade_plan.steps,
        ),
    )
    manual_checks = (
        "confirm generated systemd assets are installed at the intended target paths",
        "confirm the supervised service reaches healthy and ready states after restart",
        "confirm the pre-upgrade backup artifact and restore-validation report are retained for rollback",
    )
    return LinuxValidationPlan(
        distribution=distribution,
        phases=phases,
        manual_checks=manual_checks,
    )


def _normalize_validation_step(step: LinuxInstallStep) -> LinuxInstallStep:
    if step.name != "backup":
        return step

    return LinuxInstallStep(
        name="backup-plan",
        description="validate the upgrade backup command path without requiring a live PostgreSQL server",
        command=step.command + ("--plan-only",),
    )


def execute_linux_validation_plan(
    plan: LinuxValidationPlan,
    cwd: Path,
    *,
    phase_names: tuple[str, ...] | None = None,
) -> LinuxValidationReport:
    started_at = datetime.now(UTC)
    selected_phase_names = set(phase_names or ())
    results: list[LinuxValidationStepResult] = []

    for phase in plan.phases:
        if selected_phase_names and phase.name not in selected_phase_names:
            continue
        for step in phase.steps:
            completed = subprocess.run(
                step.command,
                check=False,
                capture_output=True,
                cwd=cwd,
                text=True,
            )
            result = LinuxValidationStepResult(
                phase_name=phase.name,
                step_name=step.name,
                description=step.description,
                command=step.command,
                return_code=completed.returncode,
                passed=completed.returncode == 0,
                stdout=completed.stdout,
                stderr=completed.stderr,
            )
            results.append(result)
            if not result.passed:
                completed_at = datetime.now(UTC)
                return LinuxValidationReport(
                    distribution=plan.distribution,
                    started_at=started_at,
                    completed_at=completed_at,
                    passed=False,
                    results=tuple(results),
                    manual_checks=plan.manual_checks,
                )

    completed_at = datetime.now(UTC)
    return LinuxValidationReport(
        distribution=plan.distribution,
        started_at=started_at,
        completed_at=completed_at,
        passed=True,
        results=tuple(results),
        manual_checks=plan.manual_checks,
    )


def linux_validation_plan_to_dict(plan: LinuxValidationPlan) -> dict[str, object]:
    return {
        "distribution": plan.distribution,
        "phases": [
            {
                "name": phase.name,
                "description": phase.description,
                "steps": [
                    {
                        "name": step.name,
                        "description": step.description,
                        "command": list(step.command),
                    }
                    for step in phase.steps
                ],
            }
            for phase in plan.phases
        ],
        "manual_checks": list(plan.manual_checks),
    }


def linux_validation_report_to_dict(report: LinuxValidationReport) -> dict[str, object]:
    return {
        "distribution": report.distribution,
        "started_at": report.started_at.isoformat(),
        "completed_at": report.completed_at.isoformat(),
        "passed": report.passed,
        "results": [
            {
                "phase_name": result.phase_name,
                "step_name": result.step_name,
                "description": result.description,
                "command": list(result.command),
                "return_code": result.return_code,
                "passed": result.passed,
                "stdout": result.stdout,
                "stderr": result.stderr,
            }
            for result in report.results
        ],
        "manual_checks": list(report.manual_checks),
    }


def render_linux_validation_plan(plan: LinuxValidationPlan) -> str:
    lines = [f"OmniBot v3 Linux VM validation plan ({plan.distribution})"]
    for index, phase in enumerate(plan.phases, start=1):
        lines.append(f"{index}. {phase.name}: {phase.description}")
        for step in phase.steps:
            lines.append(f"   - {step.name}: {' '.join(step.command)}")
    lines.append("Manual checks:")
    for index, check in enumerate(plan.manual_checks, start=1):
        lines.append(f"{index}. {check}")
    return "\n".join(lines)


def render_linux_validation_report(report: LinuxValidationReport) -> str:
    lines = [
        f"OmniBot v3 Linux VM validation report ({report.distribution})",
        f"Started at: {report.started_at.isoformat()}",
        f"Completed at: {report.completed_at.isoformat()}",
        f"Status: {'PASS' if report.passed else 'FAIL'}",
    ]
    for result in report.results:
        command_text = " ".join(result.command)
        status = "PASS" if result.passed else "FAIL"
        lines.append(f"- [{status}] {result.phase_name}/{result.step_name}: {result.description}")
        lines.append(f"  {command_text}")
        if result.stdout.strip():
            lines.append(f"  stdout: {result.stdout.strip()}")
        if result.stderr.strip():
            lines.append(f"  stderr: {result.stderr.strip()}")
    lines.append("Manual checks pending:")
    for index, check in enumerate(report.manual_checks, start=1):
        lines.append(f"{index}. {check}")
    return "\n".join(lines)
