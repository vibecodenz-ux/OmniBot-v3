"""Runtime directory permission planning for Linux-style deployments."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from omnibot_v3.domain.config import AppConfig
from omnibot_v3.domain.secrets import SecretStoragePolicy


@dataclass(frozen=True, slots=True)
class RuntimePermissionTarget:
    path: Path
    mode: int
    create: bool = True


@dataclass(frozen=True, slots=True)
class RuntimePermissionPlan:
    targets: tuple[RuntimePermissionTarget, ...]


def build_runtime_permission_plan(
    config: AppConfig,
    secret_policy: SecretStoragePolicy,
    root_directory: Path,
) -> RuntimePermissionPlan:
    data_root = root_directory / config.data_root
    logs_directory = data_root / "logs"
    runtime_directory = data_root / "runtime"
    secrets_directory = root_directory / secret_policy.filesystem_directory
    if config.secrets_directory != secret_policy.filesystem_directory:
        secrets_directory = root_directory / config.secrets_directory

    return RuntimePermissionPlan(
        targets=(
            RuntimePermissionTarget(path=data_root, mode=0o750),
            RuntimePermissionTarget(path=logs_directory, mode=0o750),
            RuntimePermissionTarget(path=runtime_directory, mode=0o750),
            RuntimePermissionTarget(path=secrets_directory, mode=0o700),
        )
    )


def apply_runtime_permission_plan(plan: RuntimePermissionPlan) -> tuple[Path, ...]:
    updated_paths: list[Path] = []
    for target in plan.targets:
        if target.create:
            target.path.mkdir(parents=True, exist_ok=True)
        os.chmod(target.path, target.mode)
        updated_paths.append(target.path)
    return tuple(updated_paths)
