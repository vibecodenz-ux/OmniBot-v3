"""Initialize runtime directories with secure permissions."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Initialize OmniBot runtime directory permissions.")
    parser.add_argument("--root-dir", default=str(REPO_ROOT))
    parser.add_argument("--data-root")
    parser.add_argument("--secrets-dir")
    parser.add_argument("--plan-only", action="store_true")
    return parser.parse_args()


def main() -> int:
    from omnibot_v3.domain import SecretStoragePolicy, load_config
    from omnibot_v3.infra import apply_runtime_permission_plan, build_runtime_permission_plan

    args = _parse_args()
    overrides: dict[str, str] = {}
    if args.data_root:
        overrides["OMNIBOT_DATA_ROOT"] = args.data_root
    if args.secrets_dir:
        overrides["OMNIBOT_SECRETS_DIR"] = args.secrets_dir

    config = load_config(overrides=overrides or None)
    secret_policy = SecretStoragePolicy(filesystem_directory=config.secrets_directory)
    plan = build_runtime_permission_plan(
        config=config,
        secret_policy=secret_policy,
        root_directory=Path(args.root_dir),
    )

    if args.plan_only:
        for target in plan.targets:
            print(f"{target.path} mode=0o{target.mode:o}")
        return 0

    updated_paths = apply_runtime_permission_plan(plan)
    for path in updated_paths:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
