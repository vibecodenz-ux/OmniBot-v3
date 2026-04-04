"""Generate or execute a one-command Linux install flow for OmniBot v3."""

from __future__ import annotations

import argparse
import getpass
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run or print the OmniBot Linux install plan.")
    parser.add_argument("--service-name", default="omnibot-v3")
    parser.add_argument("--user", default=getpass.getuser())
    parser.add_argument("--group", default=getpass.getuser())
    parser.add_argument("--working-directory", default=str(REPO_ROOT))
    parser.add_argument("--bootstrap-python-executable", default=sys.executable)
    parser.add_argument("--python-executable")
    parser.add_argument("--environment-file", default="/etc/omnibot/omnibot-v3.env")
    parser.add_argument("--backup-dir", default="/var/backups/omnibot")
    parser.add_argument(
        "--database-url", default="postgresql://omnibot:change-me@localhost:5432/omnibot"
    )
    parser.add_argument("--data-root", default="data")
    parser.add_argument("--secrets-dir", default="secrets")
    parser.add_argument("--constraints-file", default="requirements/linux-postgres-constraints.txt")
    parser.add_argument("--extras", default="api,postgres")
    parser.add_argument("--execute", action="store_true")
    return parser.parse_args()


def main() -> int:
    from omnibot_v3.infra import (
        LinuxInstallConfig,
        build_linux_install_plan,
        render_linux_install_report,
    )

    args = _parse_args()
    working_directory = Path(args.working_directory)
    runtime_python = Path(args.python_executable) if args.python_executable else working_directory / ".venv/bin/python"
    config = LinuxInstallConfig(
        repo_root=REPO_ROOT,
        service_name=args.service_name,
        user=args.user,
        group=args.group,
        working_directory=working_directory,
        bootstrap_python_executable=Path(args.bootstrap_python_executable),
        python_executable=runtime_python,
        environment_file=Path(args.environment_file),
        backup_directory=Path(args.backup_dir),
        data_root=args.data_root,
        secrets_directory=args.secrets_dir,
        database_url=args.database_url,
        constraints_file=Path(args.constraints_file),
        extras=args.extras,
    )
    plan = build_linux_install_plan(config)

    if not args.execute:
        print(render_linux_install_report(plan))
        return 0

    for step in plan.steps:
        print(f"[install] {step.name}: {step.description}")
        subprocess.run(step.command, check=True, cwd=REPO_ROOT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
