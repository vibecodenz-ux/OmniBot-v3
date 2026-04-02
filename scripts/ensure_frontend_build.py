from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_ROOT = REPO_ROOT / "frontend"
DIST_ROOT = FRONTEND_ROOT / "dist"
NODE_MODULES_ROOT = FRONTEND_ROOT / "node_modules"
SOURCE_PATTERNS = (
    "src/**/*",
    "index.html",
    "package.json",
    "package-lock.json",
    "tsconfig*.json",
    "vite.config.*",
)


def _latest_mtime(paths: list[Path]) -> float:
    timestamps = [path.stat().st_mtime for path in paths if path.exists()]
    return max(timestamps, default=0.0)


def _collect_sources() -> list[Path]:
    collected: list[Path] = []
    for pattern in SOURCE_PATTERNS:
        collected.extend(FRONTEND_ROOT.glob(pattern))
    return [path for path in collected if path.is_file()]


def _collect_dist_outputs() -> list[Path]:
    if not DIST_ROOT.exists():
        return []
    return [path for path in DIST_ROOT.rglob("*") if path.is_file()]


def _resolve_npm() -> str:
    if os.name == "nt":
        bundled_npm = REPO_ROOT / ".tools" / "node" / "node-v22.22.2-win-x64" / "npm.cmd"
        if bundled_npm.exists():
            return str(bundled_npm)
    npm = shutil.which("npm")
    if npm:
        return npm
    raise RuntimeError(
        "npm is required to build the dashboard frontend. Install Node.js and npm, "
        "or use scripts/bootstrap_debian.sh on Debian before running the dashboard."
    )


def _run(command: list[str]) -> None:
    subprocess.run(command, cwd=FRONTEND_ROOT, check=True)


def _needs_build() -> bool:
    dist_outputs = _collect_dist_outputs()
    if not dist_outputs:
        return True
    return _latest_mtime(_collect_sources()) > _latest_mtime(dist_outputs)


def main() -> int:
    npm = _resolve_npm()
    if not NODE_MODULES_ROOT.exists():
        print("[frontend-build] installing frontend dependencies")
        _run([npm, "install"])
    if _needs_build():
        print("[frontend-build] building frontend/dist")
        _run([npm, "run", "build"])
    else:
        print("[frontend-build] frontend/dist is current")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(f"[frontend-build] ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc