"""Generate or execute a clean Linux VM validation flow for OmniBot v3."""

from __future__ import annotations

import argparse
import getpass
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Plan or run clean Linux VM validation.")
    parser.add_argument("--distribution", default="ubuntu-24.04")
    parser.add_argument("--phase", choices=("full", "install", "upgrade"), default="full")
    parser.add_argument("--service-name", default="omnibot-v3")
    parser.add_argument("--user", default=getpass.getuser())
    parser.add_argument("--group", default=getpass.getuser())
    parser.add_argument("--working-directory", default=str(REPO_ROOT))
    parser.add_argument("--bootstrap-python-executable", default=sys.executable)
    parser.add_argument("--python-executable")
    parser.add_argument("--environment-file", default="/etc/omnibot/omnibot-v3.env")
    parser.add_argument("--backup-dir")
    parser.add_argument(
        "--database-url", default="postgresql://omnibot:change-me@localhost:5432/omnibot"
    )
    parser.add_argument("--data-root", default="data")
    parser.add_argument("--secrets-dir", default="secrets")
    parser.add_argument("--constraints-file", default="requirements/linux-postgres-constraints.txt")
    parser.add_argument("--extras", default="api,postgres")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--output-file")
    parser.add_argument("--execute", action="store_true")
    return parser.parse_args()


def _selected_phase_names(phase: str) -> tuple[str, ...] | None:
    if phase == "install":
        return ("install-validation",)
    if phase == "upgrade":
        return ("upgrade-validation",)
    return None


def main() -> int:
    from omnibot_v3.infra import (
        LinuxInstallConfig,
        LinuxValidationPlan,
        build_linux_validation_plan,
        execute_linux_validation_plan,
        linux_validation_plan_to_dict,
        linux_validation_report_to_dict,
        render_linux_validation_plan,
        render_linux_validation_report,
    )

    args = _parse_args()
    working_directory = Path(args.working_directory)
    runtime_python = Path(args.python_executable) if args.python_executable else working_directory / ".venv/bin/python"
    backup_directory = Path(args.backup_dir) if args.backup_dir else working_directory / ".artifacts/backups"
    config = LinuxInstallConfig(
        repo_root=REPO_ROOT,
        service_name=args.service_name,
        user=args.user,
        group=args.group,
        working_directory=working_directory,
        bootstrap_python_executable=Path(args.bootstrap_python_executable),
        python_executable=runtime_python,
        environment_file=Path(args.environment_file),
        backup_directory=backup_directory,
        data_root=args.data_root,
        secrets_directory=args.secrets_dir,
        database_url=args.database_url,
        constraints_file=Path(args.constraints_file),
        extras=args.extras,
    )
    plan = build_linux_validation_plan(config, distribution=args.distribution)
    selected_phase_names = _selected_phase_names(args.phase)

    if args.execute:
        report = execute_linux_validation_plan(plan, REPO_ROOT, phase_names=selected_phase_names)
        payload = (
            json.dumps(linux_validation_report_to_dict(report), indent=2, sort_keys=True)
            if args.format == "json"
            else render_linux_validation_report(report)
        )
    else:
        if selected_phase_names:
            filtered_plan = LinuxValidationPlan(
                distribution=plan.distribution,
                phases=tuple(
                    phase for phase in plan.phases if phase.name in set(selected_phase_names)
                ),
                manual_checks=plan.manual_checks,
            )
        else:
            filtered_plan = plan
        payload = (
            json.dumps(linux_validation_plan_to_dict(filtered_plan), indent=2, sort_keys=True)
            if args.format == "json"
            else render_linux_validation_plan(filtered_plan)
        )

    if args.output_file:
        Path(args.output_file).write_text(payload, encoding="utf-8")
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
