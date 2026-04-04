"""Verify generated or installed OmniBot v3 systemd assets."""

from __future__ import annotations

import argparse
import getpass
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify OmniBot v3 systemd service assets.")
    parser.add_argument("--service-name", default="omnibot-v3")
    parser.add_argument("--user", default=getpass.getuser())
    parser.add_argument("--group", default=getpass.getuser())
    parser.add_argument("--working-directory", default=str(REPO_ROOT))
    parser.add_argument("--python-executable")
    parser.add_argument("--environment-file", default="/etc/omnibot/omnibot-v3.env")
    parser.add_argument("--service-file")
    parser.add_argument("--env-file")
    parser.add_argument("--env", action="append", default=[])
    parser.add_argument("--check-systemctl", action="store_true")
    parser.add_argument("--require-enabled", action="store_true")
    parser.add_argument("--require-active", action="store_true")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    return parser.parse_args()


def _parse_env_pairs(raw_pairs: list[str]) -> tuple[tuple[str, str], ...]:
    merged: dict[str, str] = {"OMNIBOT_ENV": "production"}
    for raw in raw_pairs:
        if "=" not in raw:
            raise ValueError(f"Invalid environment override: {raw}")
        key, value = raw.split("=", 1)
        merged[key] = value
    return tuple(merged.items())


def _default_service_file(service_name: str) -> Path:
    return REPO_ROOT / "infra" / "generated-systemd" / f"{service_name}.service"


def _default_env_file(service_name: str) -> Path:
    return REPO_ROOT / "infra" / "generated-systemd" / f"{service_name}.env"


def _read_section_content(
    path: Path,
    *,
    section_name: str,
    missing_message: str,
) -> tuple[str | None, object | None]:
    from omnibot_v3.infra import SystemdVerificationCheck, SystemdVerificationSection

    if not path.exists():
        return None, SystemdVerificationSection(
            name=section_name,
            checks=(
                SystemdVerificationCheck(
                    name="file-exists",
                    passed=False,
                    message=missing_message,
                ),
            ),
        )

    try:
        return path.read_text(encoding="utf-8"), None
    except PermissionError:
        return None, SystemdVerificationSection(
            name=section_name,
            checks=(
                SystemdVerificationCheck(
                    name="file-readable",
                    passed=False,
                    message=f"permission denied reading {path}",
                ),
            ),
        )


def _systemctl_show(service_name: str) -> str:
    completed = subprocess.run(
        (
            "systemctl",
            "show",
            service_name,
            "--no-pager",
            "--property",
            "LoadState,FragmentPath,UnitFileState,ActiveState,User,Group,WorkingDirectory,EnvironmentFiles,ExecStart",
        ),
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip() or "systemctl show failed")
    return completed.stdout


def main() -> int:
    from omnibot_v3.infra import (
        SystemdServiceConfig,
        SystemdVerificationReport,
        parse_systemctl_show_output,
        render_systemd_verification_report,
        verify_environment_file_content,
        verify_service_unit_content,
        verify_systemctl_properties,
    )

    args = _parse_args()
    working_directory = Path(args.working_directory)
    python_executable = Path(args.python_executable) if args.python_executable else working_directory / ".venv/bin/python"
    config = SystemdServiceConfig(
        service_name=args.service_name,
        user=args.user,
        group=args.group,
        working_directory=working_directory,
        python_executable=python_executable,
        environment_file=Path(args.environment_file),
        environment=_parse_env_pairs(args.env),
    )

    sections = []

    service_file = Path(args.service_file) if args.service_file else _default_service_file(args.service_name)
    service_content, service_error_section = _read_section_content(
        service_file,
        section_name="service-file",
        missing_message=f"missing {service_file}",
    )
    if service_error_section is not None:
        sections.append(service_error_section)
    else:
        sections.append(verify_service_unit_content(service_content, config))

    env_file = Path(args.env_file) if args.env_file else _default_env_file(args.service_name)
    env_content, env_error_section = _read_section_content(
        env_file,
        section_name="environment-file",
        missing_message=f"missing {env_file}",
    )
    if env_error_section is not None:
        sections.append(env_error_section)
    else:
        sections.append(verify_environment_file_content(env_content, config))

    if args.check_systemctl:
        systemctl_output = _systemctl_show(args.service_name)
        properties = parse_systemctl_show_output(systemctl_output)
        sections.append(
            verify_systemctl_properties(
                properties,
                config,
                require_enabled=args.require_enabled,
                require_active=args.require_active,
            )
        )

    report = SystemdVerificationReport(sections=tuple(sections))
    if args.format == "json":
        print(
            json.dumps(
                {
                    "passed": report.passed,
                    "sections": [
                        {
                            "name": section.name,
                            "passed": section.passed,
                            "checks": [
                                {
                                    "name": check.name,
                                    "passed": check.passed,
                                    "message": check.message,
                                }
                                for check in section.checks
                            ],
                        }
                        for section in report.sections
                    ],
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        print(render_systemd_verification_report(report))
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())