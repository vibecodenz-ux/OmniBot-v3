"""Linux install planning for OmniBot v3."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


def _linux_path(path: Path) -> str:
    return path.as_posix()


def _extra_names(extras: str) -> tuple[str, ...]:
    return tuple(extra.strip() for extra in extras.split(",") if extra.strip())


def _editable_install_spec(extras: str) -> str:
    extra_names = _extra_names(extras)
    if not extra_names:
        return "."
    return f".[{','.join(extra_names)}]"


def _build_import_validation_command(extras: str) -> str:
    modules = ["omnibot_v3"]
    if "postgres" in _extra_names(extras):
        modules.append("psycopg")
    imports = "; ".join(f"import {module}" for module in modules)
    return f"{imports}; print('imports OK')"


@dataclass(frozen=True, slots=True)
class LinuxInstallConfig:
    repo_root: Path
    service_name: str
    user: str
    group: str
    working_directory: Path
    bootstrap_python_executable: Path
    python_executable: Path
    environment_file: Path
    backup_directory: Path
    data_root: str = "data"
    secrets_directory: str = "secrets"
    database_url: str = "postgresql://omnibot:change-me@localhost:5432/omnibot"
    systemd_output_directory: Path = Path("infra/generated-systemd")
    constraints_file: Path = Path("requirements/linux-postgres-constraints.txt")
    extras: str = "api,postgres"


@dataclass(frozen=True, slots=True)
class LinuxInstallStep:
    name: str
    command: tuple[str, ...]
    description: str


@dataclass(frozen=True, slots=True)
class LinuxInstallPlan:
    steps: tuple[LinuxInstallStep, ...]


def _build_preflight_step(
    config: LinuxInstallConfig,
    *,
    skip_permission_checks: bool = False,
) -> LinuxInstallStep:
    python_executable = _linux_path(config.bootstrap_python_executable)
    command = [
        python_executable,
        "scripts/linux_preflight.py",
        "--directory",
        config.data_root,
        "--directory",
        config.secrets_directory,
        "--command",
        "pg_dump",
        "--command",
        "pg_restore",
        "--port",
        "8000",
        "--host",
        "localhost",
    ]
    if skip_permission_checks:
        command.append("--skip-permission-checks")

    return LinuxInstallStep(
        name="preflight",
        description="validate Linux host prerequisites",
        command=tuple(command),
    )


def _build_create_venv_step(config: LinuxInstallConfig) -> LinuxInstallStep:
    bootstrap_python_executable = _linux_path(config.bootstrap_python_executable)
    venv_directory = _linux_path(config.python_executable.parent.parent)
    return LinuxInstallStep(
        name="create-venv",
        description="create or refresh the dedicated virtual environment used by the runtime",
        command=(
            bootstrap_python_executable,
            "-m",
            "venv",
            venv_directory,
        ),
    )


def _build_install_package_step(config: LinuxInstallConfig) -> LinuxInstallStep:
    python_executable = _linux_path(config.python_executable)
    constraints_file = _linux_path(config.repo_root / config.constraints_file)
    return LinuxInstallStep(
        name="install-package",
        description="install the project with pinned dependency constraints",
        command=(
            python_executable,
            "-m",
            "pip",
            "install",
            "--constraint",
            constraints_file,
            "-e",
            _editable_install_spec(config.extras),
        ),
    )


def _build_validate_imports_step(config: LinuxInstallConfig) -> LinuxInstallStep:
    python_executable = _linux_path(config.python_executable)
    return LinuxInstallStep(
        name="validate-imports",
        description="verify the pinned environment can import the installed runtime modules",
        command=(
            python_executable,
            "-c",
            _build_import_validation_command(config.extras),
        ),
    )


def _build_permissions_step(config: LinuxInstallConfig) -> LinuxInstallStep:
    working_directory = _linux_path(config.working_directory)
    python_executable = _linux_path(config.python_executable)
    return LinuxInstallStep(
        name="init-permissions",
        description="create secure runtime directories",
        command=(
            python_executable,
            "scripts/init_runtime_permissions.py",
            "--root-dir",
            working_directory,
            "--data-root",
            config.data_root,
            "--secrets-dir",
            config.secrets_directory,
        ),
    )


def _build_systemd_step(config: LinuxInstallConfig) -> LinuxInstallStep:
    working_directory = _linux_path(config.working_directory)
    python_executable = _linux_path(config.python_executable)
    environment_file = _linux_path(config.environment_file)
    systemd_output_directory = _linux_path(config.repo_root / config.systemd_output_directory)
    return LinuxInstallStep(
        name="generate-systemd",
        description="generate systemd service and environment assets",
        command=(
            python_executable,
            "scripts/generate_systemd_units.py",
            "--service-name",
            config.service_name,
            "--user",
            config.user,
            "--group",
            config.group,
            "--working-directory",
            working_directory,
            "--python-executable",
            python_executable,
            "--environment-file",
            environment_file,
            "--output-dir",
            systemd_output_directory,
            "--env",
            "OMNIBOT_ENV=production",
        ),
    )


def _build_readiness_probe_step(config: LinuxInstallConfig) -> LinuxInstallStep:
    python_executable = _linux_path(config.python_executable)
    return LinuxInstallStep(
        name="readiness-probe",
        description="verify runtime probe wiring after install assets exist",
        command=(
            python_executable,
            "scripts/runtime_probe.py",
            "--mode",
            "readiness",
            "--format",
            "text",
            "--validate-workers",
            "--reconcile-workers",
            "--connect-markets",
        ),
    )


def _build_backup_plan_step(config: LinuxInstallConfig) -> LinuxInstallStep:
    python_executable = _linux_path(config.python_executable)
    backup_directory = _linux_path(config.backup_directory)
    return LinuxInstallStep(
        name="backup-plan",
        description="show the backup command path for this install",
        command=(
            python_executable,
            "scripts/run_backup.py",
            "--database-url",
            config.database_url,
            "--output-dir",
            backup_directory,
            "--plan-only",
        ),
    )


def _build_backup_step(config: LinuxInstallConfig) -> LinuxInstallStep:
    python_executable = _linux_path(config.python_executable)
    backup_directory = _linux_path(config.backup_directory)
    return LinuxInstallStep(
        name="backup",
        description="take a pre-upgrade PostgreSQL backup",
        command=(
            python_executable,
            "scripts/run_backup.py",
            "--database-url",
            config.database_url,
            "--output-dir",
            backup_directory,
        ),
    )


def build_linux_install_plan(config: LinuxInstallConfig) -> LinuxInstallPlan:
    steps = (
        _build_preflight_step(config, skip_permission_checks=True),
        _build_create_venv_step(config),
        _build_install_package_step(config),
        _build_validate_imports_step(config),
        _build_permissions_step(config),
        _build_systemd_step(config),
        _build_readiness_probe_step(config),
        _build_backup_plan_step(config),
    )
    return LinuxInstallPlan(steps=steps)


def build_linux_upgrade_plan(config: LinuxInstallConfig) -> LinuxInstallPlan:
    steps = (
        _build_preflight_step(config),
        _build_backup_step(config),
        _build_create_venv_step(config),
        _build_install_package_step(config),
        _build_validate_imports_step(config),
        _build_permissions_step(config),
        _build_systemd_step(config),
        _build_readiness_probe_step(config),
    )
    return LinuxInstallPlan(steps=steps)


def _render_linux_plan_report(title: str, plan: LinuxInstallPlan) -> str:
    lines = [title]
    for index, step in enumerate(plan.steps, start=1):
        command_text = " ".join(step.command)
        lines.append(f"{index}. {step.name}: {step.description}")
        lines.append(f"   {command_text}")
    return "\n".join(lines)


def render_linux_install_report(plan: LinuxInstallPlan) -> str:
    return _render_linux_plan_report("OmniBot v3 Linux install plan", plan)


def render_linux_upgrade_report(plan: LinuxInstallPlan) -> str:
    return _render_linux_plan_report("OmniBot v3 Linux upgrade plan", plan)
