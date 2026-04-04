"""Systemd unit rendering and install planning for Linux deployments."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from shlex import join as shlex_join


@dataclass(frozen=True, slots=True)
class SystemdServiceConfig:
    service_name: str
    user: str
    group: str
    working_directory: Path
    python_executable: Path
    environment_file: Path
    runtime_directory_name: str = "omnibot-v3"
    state_directory_name: str = "omnibot-v3"
    logs_directory_name: str = "omnibot-v3"
    module_name: str = "omnibot_v3.api.app"
    environment: tuple[tuple[str, str], ...] = (("OMNIBOT_ENV", "production"),)
    wants: tuple[str, ...] = ("network-online.target",)
    after: tuple[str, ...] = ("network-online.target",)
    wanted_by: str = "multi-user.target"
    restart: str = "on-failure"
    restart_sec: int = 5
    umask: str = "0077"


@dataclass(frozen=True, slots=True)
class GeneratedAsset:
    path: Path
    content: str


@dataclass(frozen=True, slots=True)
class SystemdInstallPlan:
    assets: tuple[GeneratedAsset, ...]
    install_commands: tuple[tuple[str, ...], ...]


def _linux_path(path: Path) -> str:
    return path.as_posix()


def render_systemd_service(config: SystemdServiceConfig) -> str:
    exec_start = shlex_join((_linux_path(config.python_executable), "-m", config.module_name))
    environment_lines = "\n".join(
        f'Environment="{key}={value}"' for key, value in config.environment
    )
    wants = " ".join(config.wants)
    after = " ".join(config.after)
    return (
        "[Unit]\n"
        f"Description=OmniBot v3 service ({config.service_name})\n"
        f"Wants={wants}\n"
        f"After={after}\n\n"
        "[Service]\n"
        "Type=simple\n"
        f"User={config.user}\n"
        f"Group={config.group}\n"
        f"WorkingDirectory={_linux_path(config.working_directory)}\n"
        f"EnvironmentFile={_linux_path(config.environment_file)}\n"
        f"{environment_lines}\n"
        f"ExecStart={exec_start}\n"
        f"RuntimeDirectory={config.runtime_directory_name}\n"
        f"StateDirectory={config.state_directory_name}\n"
        f"LogsDirectory={config.logs_directory_name}\n"
        f"UMask={config.umask}\n"
        f"Restart={config.restart}\n"
        f"RestartSec={config.restart_sec}\n"
        "NoNewPrivileges=true\n"
        "PrivateTmp=true\n\n"
        "[Install]\n"
        f"WantedBy={config.wanted_by}\n"
    )


def render_environment_template(config: SystemdServiceConfig) -> str:
    defaults = [
        ("OMNIBOT_ENV", "production"),
        ("OMNIBOT_ADMIN_PASSWORD", "change-me"),
        ("OMNIBOT_DB_DSN", "postgresql://omnibot:change-me@localhost:5432/omnibot"),
        ("OMNIBOT_BIND_HOST", "127.0.0.1"),
        ("OMNIBOT_PORT", "8000"),
        ("OMNIBOT_LOG_LEVEL", "info"),
        ("OMNIBOT_SECRETS_DIR", "secrets"),
        ("OMNIBOT_PORTFOLIO_SNAPSHOT_INTERVAL", "60"),
    ]
    overrides = {key: value for key, value in config.environment}
    lines = [
        "# OmniBot v3 systemd environment template",
        "# Replace placeholder values before enabling the service.",
    ]
    for key, value in defaults:
        lines.append(f"{key}={overrides.pop(key, value)}")
    for key, value in overrides.items():
        lines.append(f"{key}={value}")
    return "\n".join(lines) + "\n"


def build_systemd_install_plan(
    config: SystemdServiceConfig,
    output_directory: Path,
) -> SystemdInstallPlan:
    service_asset = GeneratedAsset(
        path=output_directory / f"{config.service_name}.service",
        content=render_systemd_service(config),
    )
    env_asset = GeneratedAsset(
        path=output_directory / f"{config.service_name}.env",
        content=render_environment_template(config),
    )
    install_commands = (
        (
            "sudo",
            "install",
            "-Dm644",
            str(service_asset.path),
            f"/etc/systemd/system/{config.service_name}.service",
        ),
        (
            "sudo",
            "install",
            "-Dm640",
            str(env_asset.path),
                _linux_path(config.environment_file),
        ),
        ("sudo", "systemctl", "daemon-reload"),
        ("sudo", "systemctl", "enable", "--now", config.service_name),
    )
    return SystemdInstallPlan(assets=(service_asset, env_asset), install_commands=install_commands)
