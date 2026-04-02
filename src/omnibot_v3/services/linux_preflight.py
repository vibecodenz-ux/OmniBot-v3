"""Linux deployment preflight validation service."""

from __future__ import annotations

from dataclasses import dataclass

from omnibot_v3.domain.preflight import (
    LinuxPreflightPolicy,
    LinuxPreflightReport,
    LinuxPreflightSnapshot,
    PathPermissionRule,
    PreflightCheckResult,
    PreflightCheckSeverity,
)


def _format_mode(mode: int) -> str:
    return f"0o{mode:o}"


@dataclass(frozen=True, slots=True)
class LinuxPreflightValidator:
    policy: LinuxPreflightPolicy = LinuxPreflightPolicy()

    def validate(self, snapshot: LinuxPreflightSnapshot) -> LinuxPreflightReport:
        checks = [
            self._check_platform(snapshot),
            self._check_python(snapshot),
            self._check_disk(snapshot),
        ]

        checks.extend(self._check_commands(snapshot))
        checks.extend(self._check_writable_directories(snapshot))
        checks.extend(self._check_ports(snapshot))
        checks.extend(self._check_hosts(snapshot))
        checks.extend(self._check_permissions(snapshot))

        return LinuxPreflightReport(
            passed=all(check.passed for check in checks if check.severity == PreflightCheckSeverity.ERROR),
            checks=tuple(checks),
            checked_at=snapshot.checked_at,
        )

    def format_report(self, report: LinuxPreflightReport) -> str:
        lines = [
            f"Linux preflight: {'PASS' if report.passed else 'FAIL'}",
            f"Checked at: {report.checked_at.isoformat()}",
        ]
        for check in report.checks:
            prefix = "PASS" if check.passed else "FAIL"
            lines.append(f"[{prefix}] {check.name}: {check.message}")
        return "\n".join(lines)

    def _check_platform(self, snapshot: LinuxPreflightSnapshot) -> PreflightCheckResult:
        platform = snapshot.platform.lower()
        passed = platform == "linux"
        return PreflightCheckResult(
            name="platform",
            passed=passed,
            severity=PreflightCheckSeverity.ERROR,
            message="linux host detected" if passed else f"expected linux host, got {snapshot.platform}",
        )

    def _check_python(self, snapshot: LinuxPreflightSnapshot) -> PreflightCheckResult:
        minimum = self.policy.minimum_python_version
        current = snapshot.python_version[:2]
        passed = current >= minimum
        return PreflightCheckResult(
            name="python-version",
            passed=passed,
            severity=PreflightCheckSeverity.ERROR,
            message=(
                f"python {snapshot.python_version[0]}.{snapshot.python_version[1]}.{snapshot.python_version[2]} meets minimum"
                if passed
                else (
                    f"python {snapshot.python_version[0]}.{snapshot.python_version[1]}.{snapshot.python_version[2]} "
                    f"is below minimum {minimum[0]}.{minimum[1]}"
                )
            ),
        )

    def _check_disk(self, snapshot: LinuxPreflightSnapshot) -> PreflightCheckResult:
        passed = snapshot.free_disk_bytes >= self.policy.min_free_disk_bytes
        return PreflightCheckResult(
            name="disk-space",
            passed=passed,
            severity=PreflightCheckSeverity.ERROR,
            message=(
                f"{snapshot.free_disk_bytes} bytes free"
                if passed
                else (
                    f"{snapshot.free_disk_bytes} bytes free; requires at least "
                    f"{self.policy.min_free_disk_bytes}"
                )
            ),
        )

    def _check_commands(self, snapshot: LinuxPreflightSnapshot) -> list[PreflightCheckResult]:
        return [
            PreflightCheckResult(
                name=f"command:{command}",
                passed=command in snapshot.available_commands,
                severity=PreflightCheckSeverity.ERROR,
                message=(
                    f"required command {command} is available"
                    if command in snapshot.available_commands
                    else f"required command {command} is missing"
                ),
            )
            for command in self.policy.required_commands
        ]

    def _check_writable_directories(
        self,
        snapshot: LinuxPreflightSnapshot,
    ) -> list[PreflightCheckResult]:
        checks: list[PreflightCheckResult] = []
        for directory in self.policy.required_writable_directories:
            writable = snapshot.writable_directories.get(directory, False)
            checks.append(
                PreflightCheckResult(
                    name=f"writable-dir:{directory}",
                    passed=writable,
                    severity=PreflightCheckSeverity.ERROR,
                    message=(
                        f"directory {directory} is writable"
                        if writable
                        else f"directory {directory} is not writable"
                    ),
                )
            )
        return checks

    def _check_ports(self, snapshot: LinuxPreflightSnapshot) -> list[PreflightCheckResult]:
        checks: list[PreflightCheckResult] = []
        for port in self.policy.required_ports_available:
            available = snapshot.port_available.get(port, False)
            checks.append(
                PreflightCheckResult(
                    name=f"port:{port}",
                    passed=available,
                    severity=PreflightCheckSeverity.ERROR,
                    message=(
                        f"port {port} is available"
                        if available
                        else f"port {port} is already in use or unavailable"
                    ),
                )
            )
        return checks

    def _check_hosts(self, snapshot: LinuxPreflightSnapshot) -> list[PreflightCheckResult]:
        checks: list[PreflightCheckResult] = []
        for host in self.policy.required_resolvable_hosts:
            resolvable = snapshot.resolvable_hosts.get(host, False)
            checks.append(
                PreflightCheckResult(
                    name=f"host:{host}",
                    passed=resolvable,
                    severity=PreflightCheckSeverity.ERROR,
                    message=(
                        f"host {host} resolves"
                        if resolvable
                        else f"host {host} does not resolve"
                    ),
                )
            )
        return checks

    def _check_permissions(self, snapshot: LinuxPreflightSnapshot) -> list[PreflightCheckResult]:
        required_paths = set(self.policy.required_writable_directories)
        return [
            self._check_permission_rule(snapshot, rule)
            for rule in self.policy.permission_rules
            if rule.path in required_paths
        ]

    def _check_permission_rule(
        self,
        snapshot: LinuxPreflightSnapshot,
        rule: PathPermissionRule,
    ) -> PreflightCheckResult:
        mode = snapshot.permission_modes.get(rule.path)
        passed = mode is not None and mode <= rule.max_mode
        if mode is None:
            message = f"permissions for {rule.path} are unavailable"
        elif passed:
            message = f"{rule.path} permissions {_format_mode(mode)} are within limit {_format_mode(rule.max_mode)}"
        else:
            message = f"{rule.path} permissions {_format_mode(mode)} exceed limit {_format_mode(rule.max_mode)}"
        return PreflightCheckResult(
            name=f"permissions:{rule.path}",
            passed=passed,
            severity=PreflightCheckSeverity.ERROR,
            message=message,
        )
