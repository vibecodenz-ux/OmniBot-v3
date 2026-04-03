#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from urllib.request import urlretrieve

PRESERVE_NAMES = frozenset({".git", ".venv", ".tools", "data", "secrets", "Put github exports here"})
SYSTEMD_SERVICE_NAME = "omnibot-v3"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Apply a GitHub update or rollback for OmniBot.")
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--backup-root", required=True)
    parser.add_argument("--backup-archive-name", required=True)
    parser.add_argument("--state-file", required=True)
    parser.add_argument("--archive-url")
    parser.add_argument("--rollback-archive")
    parser.add_argument("--current-build-label", default="Unknown build")
    parser.add_argument("--current-version", default="unknown")
    parser.add_argument("--target-build-label", default="Unknown target")
    parser.add_argument("--target-version", default="unknown")
    parser.add_argument("--bind-host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--parent-pid", type=int, default=0)
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def read_update_state(state_file: Path) -> dict[str, object]:
    if not state_file.exists():
        return {}
    try:
        payload = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def write_update_state(state_file: Path, last_action: dict[str, object]) -> None:
    state_file.parent.mkdir(parents=True, exist_ok=True)
    payload = read_update_state(state_file)
    payload["last_action"] = last_action
    state_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def pid_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def stop_dashboard_process(parent_pid: int) -> None:
    if parent_pid <= 0 or parent_pid == os.getpid():
        return

    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.kill(parent_pid, signal.SIGTERM)

    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        if not pid_exists(parent_pid):
            return
        time.sleep(0.2)

    kill_signal = signal.SIGKILL if hasattr(signal, "SIGKILL") else signal.SIGTERM
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.kill(parent_pid, kill_signal)


def fail_dashboard_process(parent_pid: int) -> None:
    if parent_pid <= 0 or parent_pid == os.getpid():
        return

    kill_signal = signal.SIGKILL if hasattr(signal, "SIGKILL") else signal.SIGTERM
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.kill(parent_pid, kill_signal)


def copy_repository_children(source_root: Path, destination_root: Path, exclude_names: set[str] | frozenset[str]) -> None:
    for child in source_root.iterdir():
        if child.name in exclude_names:
            continue
        destination = destination_root / child.name
        if child.is_dir():
            shutil.copytree(child, destination, dirs_exist_ok=True)
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(child, destination)


def remove_repo_children(repo_root: Path, exclude_names: set[str] | frozenset[str]) -> None:
    for child in repo_root.iterdir():
        if child.name in exclude_names:
            continue
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()


def zip_directory_contents(source_root: Path, archive_path: Path) -> None:
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(source_root.rglob("*")):
            if path.is_dir():
                continue
            archive.write(path, arcname=path.relative_to(source_root))


def new_code_backup(
    *,
    source_root: Path,
    backup_directory: Path,
    archive_name: str,
    exclude_names: set[str] | frozenset[str],
    source_build_label: str,
    source_version: str,
) -> Path:
    stage_root = Path(tempfile.mkdtemp(prefix="omnibot-backup-stage-"))
    backup_directory.mkdir(parents=True, exist_ok=True)
    try:
        copy_repository_children(source_root, stage_root, exclude_names)
        archive_path = backup_directory / archive_name
        if archive_path.exists():
            archive_path.unlink()
        zip_directory_contents(stage_root, archive_path)
        metadata_path = archive_path.with_suffix(".json")
        metadata_path.write_text(
            json.dumps(
                {
                    "archive_name": archive_path.name,
                    "created_at": utc_now(),
                    "source_build_label": source_build_label,
                    "source_version": source_version,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        return archive_path
    finally:
        shutil.rmtree(stage_root, ignore_errors=True)


def extract_archive(archive_path: Path, destination_root: Path) -> None:
    with zipfile.ZipFile(archive_path) as archive:
        archive.extractall(destination_root)


def downloaded_source_root(extract_root: Path) -> Path:
    directories = [item for item in extract_root.iterdir() if item.is_dir()]
    if len(directories) == 1:
        return directories[0]
    raise RuntimeError("Downloaded update archive did not contain a repository root.")


def systemd_service_active(service_name: str) -> bool:
    if os.name == "nt" or shutil.which("systemctl") is None:
        return False
    try:
        result = subprocess.run(
            ["systemctl", "is-active", service_name],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return False
    return result.stdout.strip() == "active"


def restart_dashboard(repo_root: Path, *, bind_host: str, port: int, systemd_managed: bool) -> None:
    if systemd_managed:
        return

    if os.name == "nt":
        run_script = repo_root / "scripts" / "run_dashboard.ps1"
        shell = shutil.which("powershell.exe") or shutil.which("pwsh.exe")
        if shell and run_script.exists():
            creationflags = 0
            for flag_name in ("CREATE_NEW_PROCESS_GROUP", "DETACHED_PROCESS"):
                creationflags |= int(getattr(subprocess, flag_name, 0))
            subprocess.Popen(
                [
                    shell,
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(run_script),
                    "-BindHost",
                    bind_host,
                    "-Port",
                    str(port),
                ],
                cwd=repo_root,
                creationflags=creationflags,
                close_fds=True,
            )
            return
        raise RuntimeError("Updated files were applied but PowerShell could not be found to restart OmniBot.")

    run_script = repo_root / "scripts" / "run_dashboard.sh"
    if not run_script.exists():
        raise RuntimeError("Updated files were applied but scripts/run_dashboard.sh is missing.")

    subprocess.Popen(
        ["bash", str(run_script)],
        cwd=repo_root,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        start_new_session=True,
    )


def main() -> int:
    args = parse_args()
    repo_root = Path(args.repo_root).resolve()
    backup_root = Path(args.backup_root).resolve()
    state_file = Path(args.state_file).resolve()
    rollback_archive = Path(args.rollback_archive).resolve() if args.rollback_archive else None

    temp_root = Path(tempfile.mkdtemp(prefix="omnibot-update-"))
    archive_path = temp_root / "source.zip"
    extract_root = temp_root / "extract"
    extract_root.mkdir(parents=True, exist_ok=True)

    is_rollback = rollback_archive is not None
    is_update = not is_rollback
    if is_update and not args.archive_url:
        raise RuntimeError("Archive URL is required for update mode.")

    systemd_managed = systemd_service_active(SYSTEMD_SERVICE_NAME)

    try:
        write_update_state(
            state_file,
            {
                "action": "rollback" if is_rollback else "update",
                "status": "running",
                "requested_at": utc_now(),
                "current_build_label": args.current_build_label,
                "target_build_label": args.target_build_label,
                "backup_archive_name": args.backup_archive_name,
                "rollback_archive_name": rollback_archive.name if rollback_archive else None,
                "message": "Rollback is running." if is_rollback else "Update is running.",
            },
        )

        time.sleep(2)
        if not systemd_managed:
            stop_dashboard_process(args.parent_pid)

        created_backup = new_code_backup(
            source_root=repo_root,
            backup_directory=backup_root,
            archive_name=args.backup_archive_name,
            exclude_names=PRESERVE_NAMES,
            source_build_label=args.current_build_label,
            source_version=args.current_version,
        )

        if is_rollback:
            if rollback_archive is None or not rollback_archive.exists():
                raise RuntimeError(f"Rollback archive not found: {rollback_archive}")
            extract_archive(rollback_archive, extract_root)
            source_root = extract_root
        else:
            urlretrieve(args.archive_url, archive_path)
            extract_archive(archive_path, extract_root)
            source_root = downloaded_source_root(extract_root)

        remove_repo_children(repo_root, PRESERVE_NAMES)
        copy_repository_children(source_root, repo_root, PRESERVE_NAMES)

        write_update_state(
            state_file,
            {
                "action": "rollback" if is_rollback else "update",
                "status": "completed",
                "requested_at": utc_now(),
                "completed_at": utc_now(),
                "current_build_label": args.current_build_label,
                "target_build_label": args.target_build_label,
                "backup_archive_name": created_backup.name,
                "rollback_archive_name": rollback_archive.name if rollback_archive else None,
                "message": "Rollback completed and OmniBot is restarting." if is_rollback else "Update completed and OmniBot is restarting.",
            },
        )

        if systemd_managed:
            fail_dashboard_process(args.parent_pid)
            return 0

        restart_dashboard(repo_root, bind_host=args.bind_host, port=args.port, systemd_managed=systemd_managed)
        return 0
    except Exception as exc:
        write_update_state(
            state_file,
            {
                "action": "rollback" if is_rollback else "update",
                "status": "failed",
                "requested_at": utc_now(),
                "completed_at": utc_now(),
                "current_build_label": args.current_build_label,
                "target_build_label": args.target_build_label,
                "backup_archive_name": args.backup_archive_name,
                "rollback_archive_name": rollback_archive.name if rollback_archive else None,
                "message": str(exc),
            },
        )
        raise
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())