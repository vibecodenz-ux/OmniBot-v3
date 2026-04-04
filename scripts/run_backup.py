"""Run a PostgreSQL backup for OmniBot v3 and emit a manifest."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run an OmniBot PostgreSQL backup.")
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--active-schema", default="omnibot")
    parser.add_argument("--archive-schema", default="omnibot_archive")
    parser.add_argument("--prefix", default="omnibot")
    parser.add_argument("--plan-only", action="store_true")
    return parser.parse_args()


def main() -> int:
    from omnibot_v3.infra import (
        PostgresBackupConfig,
        backup_manifest_to_dict,
        build_backup_manifest,
        build_backup_plan,
    )

    args = _parse_args()
    output_directory = Path(args.output_dir)

    config = PostgresBackupConfig(
        database_url=args.database_url,
        output_directory=output_directory,
        active_schema_name=args.active_schema,
        archive_schema_name=args.archive_schema,
        backup_prefix=args.prefix,
    )
    plan = build_backup_plan(config)
    manifest = build_backup_manifest(config, plan)

    if not args.plan_only:
        output_directory.mkdir(parents=True, exist_ok=True)
        subprocess.run(plan.command, check=True)

        plan.manifest_file.write_text(
            json.dumps(backup_manifest_to_dict(manifest), indent=2, sort_keys=True),
            encoding="utf-8",
        )

    print(plan.backup_file)
    print(plan.manifest_file)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
