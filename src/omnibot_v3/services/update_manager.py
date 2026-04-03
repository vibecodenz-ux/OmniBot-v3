"""GitHub-backed application update checks and orchestration."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen
from uuid import uuid4

from omnibot_v3 import __build__, __build_label__, __version__
from omnibot_v3.domain.config import AppConfig

_BUILD_PATTERN = re.compile(r'^__build__\s*=\s*"(?P<value>[^"]+)"', re.MULTILINE)
_VERSION_PATTERN = re.compile(r'^__version__\s*=\s*"(?P<value>[^"]+)"', re.MULTILINE)


class UpdateCheckError(RuntimeError):
    """Raised when remote update metadata cannot be retrieved."""


class UpdateApplyError(RuntimeError):
    """Raised when the updater cannot be scheduled."""


@dataclass(frozen=True, slots=True)
class BuildMetadata:
    version: str
    build_number: str
    build_label: str

    def to_payload(self) -> dict[str, str]:
        return {
            "version": self.version,
            "build_number": self.build_number,
            "build_label": self.build_label,
        }


@dataclass(frozen=True, slots=True)
class BackupMetadata:
    archive_name: str
    created_at: str
    source_build_label: str | None = None
    source_version: str | None = None

    def to_payload(self) -> dict[str, str | None]:
        return {
            "archive_name": self.archive_name,
            "created_at": self.created_at,
            "source_build_label": self.source_build_label,
            "source_version": self.source_version,
        }


class UpdateManager:
    """Coordinates GitHub build checks and external updater execution."""

    def __init__(self, *, repo_root: Path, config: AppConfig) -> None:
        self._repo_root = repo_root
        self._config = config
        self._data_root = (repo_root / config.data_root).resolve()
        self._backup_root = self._data_root / "code-backups"
        self._state_file = self._data_root / "update-state.json"

    def get_build_payload(self) -> dict[str, object]:
        payload = self._local_build().to_payload()
        payload["update_source"] = self._update_source_payload()
        return payload

    def get_update_status_payload(self) -> dict[str, object]:
        state = self._read_state()
        return {
            "last_check": state.get("last_check"),
            "last_action": state.get("last_action"),
            "backups": [backup.to_payload() for backup in self._list_backups()],
        }

    def check_for_updates(self, *, timeout_seconds: float = 6.0) -> dict[str, object]:
        remote = self._fetch_remote_build(timeout_seconds=timeout_seconds)
        local = self._local_build()
        update_available = self._is_remote_newer(local=local, remote=remote)
        payload = {
            "local": {
                **local.to_payload(),
                "update_source": self._update_source_payload(),
            },
            "remote": remote.to_payload(),
            "update_available": update_available,
            "status": "update-available" if update_available else "current",
            "checked_at": datetime.now(UTC).isoformat(),
            "message": (
                f"Update available: {remote.build_label} is ready on GitHub {self._config.update_branch}."
                if update_available
                else f"You are already on the latest GitHub build: {local.build_label}."
            ),
        }
        self._update_state(last_check=payload)
        return payload

    def schedule_update(self, *, bind_host: str, port: int) -> dict[str, object]:
        check_payload = self.check_for_updates()
        if not bool(check_payload["update_available"]):
            raise UpdateApplyError("Current build already matches the latest GitHub build.")

        update_script = self._stage_update_script()
        local = self._local_build()
        target = check_payload["remote"]
        archive_url = self._archive_url()
        backup_archive_name = self._build_backup_archive_name(local)
        command = [
            self._python_launcher(),
            str(update_script),
            "--repo-root",
            str(self._repo_root),
            "--backup-root",
            str(self._backup_root),
            "--backup-archive-name",
            backup_archive_name,
            "--state-file",
            str(self._state_file),
            "--archive-url",
            archive_url,
            "--current-build-label",
            local.build_label,
            "--current-version",
            local.version,
            "--target-build-label",
            str(target["build_label"]),
            "--target-version",
            str(target["version"]),
            "--bind-host",
            bind_host,
            "--port",
            str(port),
            "--parent-pid",
            str(os.getpid()),
        ]

        creationflags = 0
        for flag_name in ("CREATE_NEW_PROCESS_GROUP", "DETACHED_PROCESS"):
            creationflags |= int(getattr(subprocess, flag_name, 0))

        try:
            subprocess.Popen(
                command,
                cwd=self._repo_root,
                creationflags=creationflags,
                close_fds=True,
                start_new_session=os.name != "nt",
            )
        except OSError as exc:
            raise UpdateApplyError(f"Unable to launch updater: {exc}") from exc

        self._update_state(
            last_action={
                "action": "update",
                "status": "scheduled",
                "requested_at": datetime.now(UTC).isoformat(),
                "message": f"Scheduled update from {local.build_label} to {target['build_label']}.",
                "current_build_label": local.build_label,
                "target_build_label": target["build_label"],
                "backup_archive_name": backup_archive_name,
            }
        )

        return {
            "accepted": True,
            "message": f"Updater scheduled for {target['build_label']}. OmniBot will restart and return to login.",
            "target": target,
            "reload_after_seconds": 6,
        }

    def schedule_rollback(self, *, bind_host: str, port: int) -> dict[str, object]:
        backups = self._list_backups()
        if not backups:
            raise UpdateApplyError("No rollback backup is available yet.")

        backup = backups[0]
        rollback_archive = self._backup_root / backup.archive_name
        if not rollback_archive.exists():
            raise UpdateApplyError("Latest rollback backup could not be found on disk.")

        current = self._local_build()
        rollback_script = self._stage_update_script()
        safety_backup_archive_name = self._build_backup_archive_name(current, prefix="pre-rollback")
        command = [
            self._python_launcher(),
            str(rollback_script),
            "--repo-root",
            str(self._repo_root),
            "--backup-root",
            str(self._backup_root),
            "--backup-archive-name",
            safety_backup_archive_name,
            "--state-file",
            str(self._state_file),
            "--rollback-archive",
            str(rollback_archive),
            "--current-build-label",
            current.build_label,
            "--current-version",
            current.version,
            "--target-build-label",
            str(backup.source_build_label or "Rollback backup"),
            "--target-version",
            str(backup.source_version or "unknown"),
            "--bind-host",
            bind_host,
            "--port",
            str(port),
            "--parent-pid",
            str(os.getpid()),
        ]

        creationflags = 0
        for flag_name in ("CREATE_NEW_PROCESS_GROUP", "DETACHED_PROCESS"):
            creationflags |= int(getattr(subprocess, flag_name, 0))

        try:
            subprocess.Popen(
                command,
                cwd=self._repo_root,
                creationflags=creationflags,
                close_fds=True,
                start_new_session=os.name != "nt",
            )
        except OSError as exc:
            raise UpdateApplyError(f"Unable to launch rollback: {exc}") from exc

        self._update_state(
            last_action={
                "action": "rollback",
                "status": "scheduled",
                "requested_at": datetime.now(UTC).isoformat(),
                "message": f"Scheduled rollback from {current.build_label} using {backup.archive_name}.",
                "current_build_label": current.build_label,
                "target_build_label": backup.source_build_label or "Rollback backup",
                "rollback_archive_name": backup.archive_name,
                "backup_archive_name": safety_backup_archive_name,
            }
        )

        return {
            "accepted": True,
            "message": f"Rollback scheduled using {backup.archive_name}. OmniBot will restart and return to login.",
            "target": {
                "version": backup.source_version or "unknown",
                "build_number": "rollback",
                "build_label": backup.source_build_label or "Rollback backup",
            },
            "reload_after_seconds": 6,
        }

    def _local_build(self) -> BuildMetadata:
        return BuildMetadata(
            version=__version__,
            build_number=__build__,
            build_label=__build_label__,
        )

    def _fetch_remote_build(self, *, timeout_seconds: float) -> BuildMetadata:
        try:
            with urlopen(self._metadata_url(), timeout=timeout_seconds) as response:
                payload = response.read().decode("utf-8")
        except (URLError, OSError, TimeoutError, ValueError) as exc:
            raise UpdateCheckError(f"Unable to reach GitHub update metadata: {exc}") from exc

        version = self._match_metadata(payload, _VERSION_PATTERN, "version")
        build_number = self._match_metadata(payload, _BUILD_PATTERN, "build")
        return BuildMetadata(
            version=version,
            build_number=build_number,
            build_label=f"Build:{build_number}",
        )

    def _stage_update_script(self) -> Path:
        source_script = self._repo_root / "scripts" / "update_from_github.py"
        if not source_script.exists():
            raise UpdateApplyError("Updater script is missing from scripts/update_from_github.py.")

        staged_path = Path(tempfile.gettempdir()) / f"omnibot-update-{uuid4().hex}.py"
        shutil.copy2(source_script, staged_path)
        return staged_path

    @staticmethod
    def _python_launcher() -> str:
        if sys.executable:
            return sys.executable
        raise UpdateApplyError("Unable to determine the current Python executable for the updater.")

    def _read_state(self) -> dict[str, object]:
        if not self._state_file.exists():
            return {}
        try:
            payload = json.loads(self._state_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _update_state(self, *, last_check: dict[str, object] | None = None, last_action: dict[str, object] | None = None) -> None:
        self._data_root.mkdir(parents=True, exist_ok=True)
        state = self._read_state()
        if last_check is not None:
            state["last_check"] = last_check
        if last_action is not None:
            state["last_action"] = last_action
        self._state_file.write_text(json.dumps(state, indent=2), encoding="utf-8")

    def _list_backups(self) -> list[BackupMetadata]:
        if not self._backup_root.exists():
            return []
        backups: list[BackupMetadata] = []
        for archive_path in sorted(self._backup_root.glob("*.zip"), key=lambda path: path.stat().st_mtime, reverse=True):
            metadata_path = archive_path.with_suffix(".json")
            metadata_payload: dict[str, object] = {}
            if metadata_path.exists():
                try:
                    loaded_payload = json.loads(metadata_path.read_text(encoding="utf-8"))
                    if isinstance(loaded_payload, dict):
                        metadata_payload = loaded_payload
                except (OSError, json.JSONDecodeError):
                    metadata_payload = {}
            created_at = metadata_payload.get("created_at")
            if not isinstance(created_at, str):
                created_at = datetime.fromtimestamp(archive_path.stat().st_mtime, tz=UTC).isoformat()
            backups.append(
                BackupMetadata(
                    archive_name=archive_path.name,
                    created_at=created_at,
                    source_build_label=(
                        str(metadata_payload.get("source_build_label"))
                        if metadata_payload.get("source_build_label") is not None
                        else None
                    ),
                    source_version=(
                        str(metadata_payload.get("source_version"))
                        if metadata_payload.get("source_version") is not None
                        else None
                    ),
                )
            )
        return backups

    @staticmethod
    def _build_backup_archive_name(build: BuildMetadata, *, prefix: str = "code-backup") -> str:
        sanitized_label = build.build_label.replace(":", "-").replace(" ", "-")
        timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        return f"{prefix}-{sanitized_label}-{timestamp}.zip"

    def _metadata_url(self) -> str:
        return (
            f"https://raw.githubusercontent.com/{self._config.update_repo}/"
            f"{self._config.update_branch}/src/omnibot_v3/__init__.py"
        )

    def _archive_url(self) -> str:
        return (
            f"https://github.com/{self._config.update_repo}/archive/refs/heads/"
            f"{self._config.update_branch}.zip"
        )

    def _update_source_payload(self) -> dict[str, str]:
        return {
            "repo": self._config.update_repo,
            "branch": self._config.update_branch,
            "metadata_url": self._metadata_url(),
            "archive_url": self._archive_url(),
        }

    @staticmethod
    def _match_metadata(payload: str, pattern: re.Pattern[str], label: str) -> str:
        match = pattern.search(payload)
        if match is None:
            raise UpdateCheckError(f"GitHub metadata is missing a {label} value.")
        return match.group("value")

    @staticmethod
    def _is_remote_newer(*, local: BuildMetadata, remote: BuildMetadata) -> bool:
        local_digits = "".join(character for character in local.build_number if character.isdigit())
        remote_digits = "".join(character for character in remote.build_number if character.isdigit())
        if local_digits and remote_digits:
            return int(remote_digits) > int(local_digits)
        return (remote.version, remote.build_number) > (local.version, local.build_number)