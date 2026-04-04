#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import time
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from urllib.request import urlretrieve

PRESERVE_NAMES = frozenset({".git", ".venv", ".tools", "data", "secrets", "Put github exports here"})
SYSTEMD_SERVICE_NAME = "omnibot-v3"
BUILD_PATTERN = re.compile(r'^__build__\s*=\s*"(?P<value>[^"]+)"', re.MULTILINE)
VERSION_PATTERN = re.compile(r'^__version__\s*=\s*"(?P<value>[^"]+)"', re.MULTILINE)


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
    parser.add_argument("--service-name", default=SYSTEMD_SERVICE_NAME)
    parser.add_argument("--install-extras", default="api")
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


def update_action_payload(
    *,
    action: str,
    status: str,
    current_build_label: str,
    target_build_label: str,
    backup_archive_name: str,
    rollback_archive_name: str | None,
    message: str,
    stage: str,
    work_root: Path | None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "action": action,
        "status": status,
        "requested_at": utc_now(),
        "current_build_label": current_build_label,
        "target_build_label": target_build_label,
        "backup_archive_name": backup_archive_name,
        "rollback_archive_name": rollback_archive_name,
        "stage": stage,
        "message": message,
    }
    if status in {"completed", "failed"}:
        payload["completed_at"] = utc_now()
    if work_root is not None:
        payload["work_root"] = str(work_root)
    return payload


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


def repo_python_executable(repo_root: Path) -> Path:
    if os.name == "nt":
        return repo_root / ".venv" / "Scripts" / "python.exe"
    return repo_root / ".venv" / "bin" / "python"


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


def move_repository_children(source_root: Path, destination_root: Path, exclude_names: set[str] | frozenset[str]) -> None:
    destination_root.mkdir(parents=True, exist_ok=True)
    for child in list(source_root.iterdir()):
        if child.name in exclude_names:
            continue
        shutil.move(str(child), destination_root / child.name)


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


def extracted_source_root(extract_root: Path) -> Path:
    if (extract_root / "pyproject.toml").exists():
        return extract_root

    directories = [item for item in extract_root.iterdir() if item.is_dir()]
    if len(directories) == 1 and (directories[0] / "pyproject.toml").exists():
        return directories[0]

    raise RuntimeError("Downloaded update archive did not contain a repository root.")


def read_source_metadata(source_root: Path) -> tuple[str, str]:
    init_path = source_root / "src" / "omnibot_v3" / "__init__.py"
    if not init_path.exists():
        raise RuntimeError(f"Updated source is missing build metadata: {init_path}")

    payload = init_path.read_text(encoding="utf-8")
    build_match = BUILD_PATTERN.search(payload)
    version_match = VERSION_PATTERN.search(payload)
    if build_match is None or version_match is None:
        raise RuntimeError("Updated source is missing version or build metadata.")
    return version_match.group("value"), build_match.group("value")


def validate_source_metadata(source_root: Path, *, target_version: str, target_build_label: str) -> None:
    version, build_number = read_source_metadata(source_root)
    expected_build_number = target_build_label.split(":", 1)[-1]
    resolved_build_label = f"Build:{build_number}"
    if version != target_version:
        raise RuntimeError(
            f"Updated source version mismatch: expected {target_version}, found {version}."
        )
    if resolved_build_label != target_build_label:
        raise RuntimeError(
            f"Updated source build mismatch: expected {target_build_label}, found {resolved_build_label}."
        )
    if build_number != expected_build_number:
        raise RuntimeError(
            f"Updated source build number mismatch: expected {expected_build_number}, found {build_number}."
        )


def stage_source_tree(source_root: Path, destination_root: Path) -> None:
    if destination_root.exists():
        shutil.rmtree(destination_root)
    destination_root.mkdir(parents=True, exist_ok=True)
    copy_repository_children(source_root, destination_root, PRESERVE_NAMES)


def install_target(source_root: Path, install_extras: str) -> str:
    if not install_extras.strip():
        return str(source_root)
    return f"{source_root}[{install_extras}]"


def frontend_build_ready(source_root: Path) -> bool:
    dist_root = source_root / "frontend" / "dist"
    assets_root = dist_root / "assets"
    return dist_root.exists() and assets_root.exists()


def ensure_frontend_runtime_assets(source_root: Path, repo_root: Path) -> None:
    if frontend_build_ready(source_root):
        return

    python_path = repo_python_executable(repo_root)
    if not python_path.exists():
        raise RuntimeError(f"Virtual environment is missing: {python_path}")

    build_script = source_root / "scripts" / "ensure_frontend_build.py"
    if not build_script.exists():
        raise RuntimeError(
            "Missing frontend build output under frontend/dist and scripts/ensure_frontend_build.py "
            "is not available to rebuild it."
        )

    completed = subprocess.run(
        [str(python_path), str(build_script)],
        cwd=source_root,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        stdout = completed.stdout.strip()
        stderr = completed.stderr.strip()
        details = "\n".join(part for part in (stdout, stderr) if part)
        raise RuntimeError(f"Frontend build failed during staged update.\n{details}".strip())

    if not frontend_build_ready(source_root):
        raise RuntimeError(
            "Frontend build completed but frontend/dist is still missing required assets."
        )


def sync_runtime_environment(source_root: Path, repo_root: Path, install_extras: str) -> None:
    python_path = repo_python_executable(repo_root)
    if not python_path.exists():
        raise RuntimeError(f"Virtual environment is missing: {python_path}")

    command = [
        str(python_path),
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "-e",
        install_target(source_root, install_extras),
    ]
    completed = subprocess.run(
        command,
        cwd=source_root,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        stdout = completed.stdout.strip()
        stderr = completed.stderr.strip()
        details = "\n".join(part for part in (stdout, stderr) if part)
        raise RuntimeError(f"Dependency sync failed.\n{details}".strip())


def transactional_switch_repository(repo_root: Path, staged_root: Path, original_root: Path) -> None:
    original_root.mkdir(parents=True, exist_ok=True)
    move_repository_children(repo_root, original_root, PRESERVE_NAMES)
    try:
        move_repository_children(staged_root, repo_root, PRESERVE_NAMES)
    except Exception:
        restore_repository_children(repo_root, original_root)
        raise


def restore_repository_children(repo_root: Path, original_root: Path) -> None:
    remove_repo_children(repo_root, PRESERVE_NAMES)
    move_repository_children(original_root, repo_root, PRESERVE_NAMES)


def smoke_validate_repo(repo_root: Path) -> None:
    python_path = repo_python_executable(repo_root)
    if not python_path.exists():
        raise RuntimeError(f"Virtual environment is missing after cutover: {python_path}")

    environment = dict(os.environ)
    python_path_entry = str(repo_root / "src")
    if environment.get("PYTHONPATH"):
        environment["PYTHONPATH"] = python_path_entry + os.pathsep + environment["PYTHONPATH"]
    else:
        environment["PYTHONPATH"] = python_path_entry

    command = [
        str(python_path),
        "-c",
        (
            "from omnibot_v3 import __build_label__; "
            "from omnibot_v3.api.app import create_app; "
            "app = create_app(); "
            "print(__build_label__); "
            "print(app.title)"
        ),
    ]
    completed = subprocess.run(
        command,
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    if completed.returncode != 0:
        stdout = completed.stdout.strip()
        stderr = completed.stderr.strip()
        details = "\n".join(part for part in (stdout, stderr) if part)
        raise RuntimeError(f"Post-update smoke validation failed.\n{details}".strip())


def running_inside_systemd_service(service_name: str) -> bool:
    if os.name == "nt":
        return False

    if any(os.environ.get(name) for name in ("INVOCATION_ID", "JOURNAL_STREAM", "SYSTEMD_EXEC_PID")):
        return True

    cgroup_path = Path("/proc/self/cgroup")
    if not cgroup_path.exists():
        return False

    try:
        cgroup_content = cgroup_path.read_text(encoding="utf-8")
    except OSError:
        return False

    return f"{service_name}.service" in cgroup_content


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
    work_root = state_file.parent / "update-work" / datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
    archive_path = temp_root / "source.zip"
    extract_root = temp_root / "extract"
    extract_root.mkdir(parents=True, exist_ok=True)
    staged_root = work_root / "staged-release"
    original_root = work_root / "original-release"

    is_rollback = rollback_archive is not None
    is_update = not is_rollback
    if is_update and not args.archive_url:
        raise RuntimeError("Archive URL is required for update mode.")

    service_name = args.service_name or SYSTEMD_SERVICE_NAME
    systemd_managed = running_inside_systemd_service(service_name) or systemd_service_active(service_name)
    action_name = "rollback" if is_rollback else "update"
    rollback_archive_name = rollback_archive.name if rollback_archive else None
    created_backup_name = args.backup_archive_name
    switched = False

    try:
        work_root.mkdir(parents=True, exist_ok=True)
        write_update_state(
            state_file,
            update_action_payload(
                action=action_name,
                status="running",
                current_build_label=args.current_build_label,
                target_build_label=args.target_build_label,
                backup_archive_name=args.backup_archive_name,
                rollback_archive_name=rollback_archive_name,
                message="Preparing staged update files." if is_update else "Preparing staged rollback files.",
                stage="preflight",
                work_root=work_root,
            ),
        )

        created_backup = new_code_backup(
            source_root=repo_root,
            backup_directory=backup_root,
            archive_name=args.backup_archive_name,
            exclude_names=PRESERVE_NAMES,
            source_build_label=args.current_build_label,
            source_version=args.current_version,
        )
        created_backup_name = created_backup.name

        if is_rollback:
            if rollback_archive is None or not rollback_archive.exists():
                raise RuntimeError(f"Rollback archive not found: {rollback_archive}")
            extract_archive(rollback_archive, extract_root)
            source_root = extracted_source_root(extract_root)
        else:
            write_update_state(
                state_file,
                update_action_payload(
                    action=action_name,
                    status="running",
                    current_build_label=args.current_build_label,
                    target_build_label=args.target_build_label,
                    backup_archive_name=created_backup_name,
                    rollback_archive_name=rollback_archive_name,
                    message="Downloading target build from the configured update source.",
                    stage="download",
                    work_root=work_root,
                ),
            )
            urlretrieve(args.archive_url, archive_path)
            extract_archive(archive_path, extract_root)
            source_root = extracted_source_root(extract_root)

        write_update_state(
            state_file,
            update_action_payload(
                action=action_name,
                status="running",
                current_build_label=args.current_build_label,
                target_build_label=args.target_build_label,
                backup_archive_name=created_backup_name,
                rollback_archive_name=rollback_archive_name,
                message="Validating staged build metadata, syncing dependencies, and preparing frontend assets.",
                stage="validate",
                work_root=work_root,
            ),
        )
        validate_source_metadata(
            source_root,
            target_version=args.target_version,
            target_build_label=args.target_build_label,
        )
        stage_source_tree(source_root, staged_root)
        sync_runtime_environment(staged_root, repo_root, args.install_extras)
        ensure_frontend_runtime_assets(staged_root, repo_root)

        write_update_state(
            state_file,
            update_action_payload(
                action=action_name,
                status="running",
                current_build_label=args.current_build_label,
                target_build_label=args.target_build_label,
                backup_archive_name=created_backup_name,
                rollback_archive_name=rollback_archive_name,
                message="Switching the live code tree to the staged build.",
                stage="switch",
                work_root=work_root,
            ),
        )
        transactional_switch_repository(repo_root, staged_root, original_root)
        switched = True
        sync_runtime_environment(repo_root, repo_root, args.install_extras)
        smoke_validate_repo(repo_root)

        write_update_state(
            state_file,
            update_action_payload(
                action=action_name,
                status="completed",
                current_build_label=args.current_build_label,
                target_build_label=args.target_build_label,
                backup_archive_name=created_backup_name,
                rollback_archive_name=rollback_archive_name,
                message="Rollback completed and OmniBot is restarting." if is_rollback else "Update completed and OmniBot is restarting.",
                stage="restart",
                work_root=work_root,
            ),
        )

        if systemd_managed:
            fail_dashboard_process(args.parent_pid)
            return 0

        time.sleep(1)
        stop_dashboard_process(args.parent_pid)
        restart_dashboard(repo_root, bind_host=args.bind_host, port=args.port, systemd_managed=systemd_managed)
        return 0
    except Exception as exc:
        if switched:
            with contextlib.suppress(Exception):
                restore_repository_children(repo_root, original_root)
        write_update_state(
            state_file,
            update_action_payload(
                action=action_name,
                status="failed",
                current_build_label=args.current_build_label,
                target_build_label=args.target_build_label,
                backup_archive_name=created_backup_name,
                rollback_archive_name=rollback_archive_name,
                message=str(exc),
                stage="rollback" if switched else "failed",
                work_root=work_root,
            ),
        )
        raise
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())