"""Verification helpers for generated and installed systemd assets."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from omnibot_v3.infra.systemd_units import SystemdServiceConfig


@dataclass(frozen=True, slots=True)
class SystemdVerificationCheck:
    name: str
    passed: bool
    message: str


@dataclass(frozen=True, slots=True)
class SystemdVerificationSection:
    name: str
    checks: tuple[SystemdVerificationCheck, ...]

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)


@dataclass(frozen=True, slots=True)
class SystemdVerificationReport:
    sections: tuple[SystemdVerificationSection, ...]

    @property
    def passed(self) -> bool:
        return all(section.passed for section in self.sections)


def parse_environment_file(content: str) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        pairs[key.strip()] = value.strip()
    return pairs


def parse_systemctl_show_output(content: str) -> dict[str, str]:
    properties: dict[str, str] = {}
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or "=" not in line:
            continue
        key, value = line.split("=", 1)
        properties[key] = value
    return properties


def verify_service_unit_content(
    content: str,
    config: SystemdServiceConfig,
) -> SystemdVerificationSection:
    expected_exec_start = f"ExecStart={config.python_executable.as_posix()} -m {config.module_name}"
    checks = (
        _contains_line("user", content, f"User={config.user}"),
        _contains_line("group", content, f"Group={config.group}"),
        _contains_line(
            "working-directory",
            content,
            f"WorkingDirectory={config.working_directory.as_posix()}",
        ),
        _contains_line(
            "environment-file",
            content,
            f"EnvironmentFile={config.environment_file.as_posix()}",
        ),
        _contains_line("exec-start", content, expected_exec_start),
        _contains_line("restart", content, f"Restart={config.restart}"),
        _contains_line("wanted-by", content, f"WantedBy={config.wanted_by}"),
    )
    return SystemdVerificationSection(name="service-file", checks=checks)


def verify_environment_file_content(
    content: str,
    config: SystemdServiceConfig,
) -> SystemdVerificationSection:
    values = parse_environment_file(content)
    default_keys = (
        "OMNIBOT_ENV",
        "OMNIBOT_ADMIN_PASSWORD",
        "OMNIBOT_DB_DSN",
        "OMNIBOT_BIND_HOST",
        "OMNIBOT_PORT",
        "OMNIBOT_LOG_LEVEL",
        "OMNIBOT_SECRETS_DIR",
        "OMNIBOT_PORTFOLIO_SNAPSHOT_INTERVAL",
    )
    checks = [
        _has_env_key(values, key)
        for key in default_keys
    ]
    checks.extend(
        _matches_env_value(values, key, value)
        for key, value in config.environment
    )
    return SystemdVerificationSection(name="environment-file", checks=tuple(checks))


def verify_systemctl_properties(
    properties: Mapping[str, str],
    config: SystemdServiceConfig,
    *,
    require_enabled: bool = False,
    require_active: bool = False,
) -> SystemdVerificationSection:
    expected_fragment = f"/etc/systemd/system/{config.service_name}.service"
    checks = [
        _matches_property(properties, "LoadState", "loaded"),
        _matches_property(properties, "FragmentPath", expected_fragment),
        _matches_property(properties, "User", config.user),
        _matches_property(properties, "Group", config.group),
        _matches_property(properties, "WorkingDirectory", config.working_directory.as_posix()),
        _property_contains(
            properties,
            "EnvironmentFiles",
            config.environment_file.as_posix(),
        ),
        _property_contains(
            properties,
            "ExecStart",
            f"{config.python_executable.as_posix()} -m {config.module_name}",
        ),
    ]
    if require_enabled:
        checks.append(_matches_property(properties, "UnitFileState", "enabled"))
    if require_active:
        checks.append(_matches_property(properties, "ActiveState", "active"))
    return SystemdVerificationSection(name="systemctl", checks=tuple(checks))


def render_systemd_verification_report(report: SystemdVerificationReport) -> str:
    lines = [
        "OmniBot v3 systemd verification report",
        f"Status: {'PASS' if report.passed else 'FAIL'}",
    ]
    for section in report.sections:
        lines.append(f"Section: {section.name}")
        for check in section.checks:
            status = "PASS" if check.passed else "FAIL"
            lines.append(f"- [{status}] {check.name}: {check.message}")
    return "\n".join(lines)


def _contains_line(name: str, content: str, expected_line: str) -> SystemdVerificationCheck:
    passed = expected_line in content
    message = expected_line if passed else f"missing {expected_line}"
    return SystemdVerificationCheck(name=name, passed=passed, message=message)


def _has_env_key(values: Mapping[str, str], key: str) -> SystemdVerificationCheck:
    passed = key in values
    message = key if passed else f"missing {key}"
    return SystemdVerificationCheck(name=f"env-key:{key}", passed=passed, message=message)


def _matches_env_value(
    values: Mapping[str, str],
    key: str,
    expected_value: str,
) -> SystemdVerificationCheck:
    actual_value = values.get(key)
    passed = actual_value == expected_value
    message = (
        f"{key}={actual_value}" if passed else f"expected {key}={expected_value}, found {actual_value}"
    )
    return SystemdVerificationCheck(name=f"env-value:{key}", passed=passed, message=message)


def _matches_property(
    properties: Mapping[str, str],
    key: str,
    expected_value: str,
) -> SystemdVerificationCheck:
    actual_value = properties.get(key)
    passed = actual_value == expected_value
    message = (
        f"{key}={actual_value}" if passed else f"expected {key}={expected_value}, found {actual_value}"
    )
    return SystemdVerificationCheck(name=f"property:{key}", passed=passed, message=message)


def _property_contains(
    properties: Mapping[str, str],
    key: str,
    expected_fragment: str,
) -> SystemdVerificationCheck:
    actual_value = properties.get(key, "")
    passed = expected_fragment in actual_value
    message = (
        f"{key} contains {expected_fragment}"
        if passed
        else f"expected {key} to contain {expected_fragment}, found {actual_value}"
    )
    return SystemdVerificationCheck(name=f"property:{key}", passed=passed, message=message)