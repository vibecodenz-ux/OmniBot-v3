"""Emit a restore validation report for an OmniBot PostgreSQL backup."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate an OmniBot restore validation report.")
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--backup-file", required=True)
    parser.add_argument("--active-schema", default="omnibot")
    parser.add_argument("--archive-schema", default="omnibot_archive")
    parser.add_argument("--output-file")
    return parser.parse_args()


def main() -> int:
    from omnibot_v3.infra import (
        PostgresBackupConfig,
        build_restore_validation_report,
        restore_validation_report_to_dict,
    )

    args = _parse_args()
    backup_file = Path(args.backup_file)
    config = PostgresBackupConfig(
        database_url=args.database_url,
        output_directory=backup_file.parent,
        active_schema_name=args.active_schema,
        archive_schema_name=args.archive_schema,
    )
    report = build_restore_validation_report(config, backup_file)
    payload = json.dumps(restore_validation_report_to_dict(report), indent=2, sort_keys=True)

    if args.output_file:
        Path(args.output_file).write_text(payload, encoding="utf-8")
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
