#!/usr/bin/env python3
"""OmniBot v3 local bootstrap script."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.resolve()
VENV_DIR = REPO_ROOT / ".venv"
MIN_PYTHON = (3, 11)
DEFAULT_EXTRAS = "api"


def _fail(msg: str) -> None:
    print(f"[bootstrap] ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def _info(msg: str) -> None:
    print(f"[bootstrap] {msg}")


def _check_python_version() -> None:
    version = sys.version_info[:2]
    if version < MIN_PYTHON:
        _fail(
            f"Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ is required; "
            f"running {version[0]}.{version[1]}."
        )
    _info(f"Python {version[0]}.{version[1]} — OK")


def _venv_python() -> Path:
    """Return the path to the venv Python binary."""
    if sys.platform == "win32":
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def _create_venv(skip: bool) -> None:
    if skip:
        _info("--skip-venv: using current interpreter.")
        return
    if VENV_DIR.exists():
        _info(f".venv already exists at {VENV_DIR}; skipping creation.")
        return
    _info(f"Creating virtual environment at {VENV_DIR} …")
    subprocess.run([sys.executable, "-m", "venv", str(VENV_DIR)], check=True)
    _info("Virtual environment created.")


def _install(extras: str, skip_venv: bool) -> None:
    python = sys.executable if skip_venv else str(_venv_python())
    spec = f".[{extras}]" if extras else "."
    _info(f"Installing {spec} …")
    subprocess.run(
        [python, "-m", "pip", "install", "--upgrade", "pip"],
        check=True,
        cwd=REPO_ROOT,
    )
    subprocess.run(
        [python, "-m", "pip", "install", "-e", spec],
        check=True,
        cwd=REPO_ROOT,
    )
    _info("Installation complete.")


def _preflight(skip_venv: bool) -> None:
    python = sys.executable if skip_venv else str(_venv_python())
    _info("Running preflight checks …")

    # Verify the package itself is importable.
    result = subprocess.run(
        [python, "-c", "import omnibot_v3; print('omnibot_v3 OK')"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    if result.returncode != 0:
        _fail(f"Could not import omnibot_v3:\n{result.stderr}")
    _info(f"  {result.stdout.strip()}")

    _info("All preflight checks passed.")


def main() -> None:
    parser = argparse.ArgumentParser(description="OmniBot v3 bootstrap")
    parser.add_argument(
        "--extras",
        default=DEFAULT_EXTRAS,
        help=f"pip extras to install (default: {DEFAULT_EXTRAS!r})",
    )
    parser.add_argument(
        "--skip-venv",
        action="store_true",
        help="Skip venv creation and use the current interpreter",
    )
    args = parser.parse_args()

    _check_python_version()
    _create_venv(args.skip_venv)
    _install(args.extras, args.skip_venv)
    _preflight(args.skip_venv)

    _info("")
    _info("Bootstrap complete. Activate your virtual environment:")
    if sys.platform == "win32":
        _info(f"    {VENV_DIR}\\Scripts\\activate")
        _info("For local dashboard use on Windows, run: powershell -ExecutionPolicy Bypass -File scripts/run_dashboard.ps1")
    else:
        _info(f"    source {VENV_DIR}/bin/activate")
        _info("For local dashboard use, run: bash scripts/run_dashboard.sh")


if __name__ == "__main__":
    main()
