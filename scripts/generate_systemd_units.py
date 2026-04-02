"""Generate systemd deployment assets for OmniBot v3."""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate systemd files for OmniBot v3.")
    parser.add_argument("--service-name", default="omnibot-v3")
    parser.add_argument("--user", default=getpass.getuser())
    parser.add_argument("--group", default=getpass.getuser())
    parser.add_argument("--working-directory", default=str(REPO_ROOT))
    parser.add_argument("--python-executable", default=sys.executable)
    parser.add_argument("--environment-file", default="/etc/omnibot/omnibot-v3.env")
    parser.add_argument("--output-dir", default=str(REPO_ROOT / "infra" / "generated-systemd"))
    parser.add_argument(
        "--env",
        action="append",
        default=[],
        help="Additional KEY=VALUE environment entries embedded in the service unit.",
    )
    return parser.parse_args()


def _parse_env_pairs(raw_pairs: list[str]) -> tuple[tuple[str, str], ...]:
    pairs: list[tuple[str, str]] = []
    for raw in raw_pairs:
        if "=" not in raw:
            raise ValueError(f"Invalid environment override: {raw}")
        key, value = raw.split("=", 1)
        pairs.append((key, value))
    return tuple(pairs)


def main() -> int:
    from omnibot_v3.infra import SystemdServiceConfig, build_systemd_install_plan

    args = _parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    config = SystemdServiceConfig(
        service_name=args.service_name,
        user=args.user,
        group=args.group,
        working_directory=Path(args.working_directory),
        python_executable=Path(args.python_executable),
        environment_file=Path(args.environment_file),
        environment=_parse_env_pairs(args.env),
    )
    plan = build_systemd_install_plan(config=config, output_directory=output_dir)

    for asset in plan.assets:
        asset.path.parent.mkdir(parents=True, exist_ok=True)
        asset.path.write_text(asset.content, encoding="utf-8")
        print(f"wrote {asset.path}")

    print("\nSuggested install commands:")
    for command in plan.install_commands:
        print(" ".join(command))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
