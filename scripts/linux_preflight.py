"""Linux deployment preflight script for OmniBot v3."""

from __future__ import annotations

import argparse
import os
import shutil
import socket
import stat
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Linux deployment preflight checks.")
    parser.add_argument(
        "--directory",
        action="append",
        dest="directories",
        default=None,
        help="Directory that must exist or be creatable and writable. Can be repeated.",
    )
    parser.add_argument(
        "--command",
        action="append",
        dest="commands",
        default=None,
        help="Command that must be available in PATH. Can be repeated.",
    )
    parser.add_argument(
        "--port",
        action="append",
        type=int,
        dest="ports",
        default=None,
        help="TCP port that must be available. Can be repeated.",
    )
    parser.add_argument(
        "--host",
        action="append",
        dest="hosts",
        default=None,
        help="Hostname that must resolve. Can be repeated.",
    )
    parser.add_argument(
        "--min-free-gb",
        type=float,
        default=1.0,
        help="Minimum free disk space in GiB required on the repository filesystem.",
    )
    parser.add_argument(
        "--skip-permission-checks",
        action="store_true",
        help="Skip directory permission-mode checks. Useful for clean installs before runtime permissions are initialized.",
    )
    return parser.parse_args()


def _is_writable_directory(path: Path) -> bool:
    if not path.exists():
        parent = path.parent
        while not parent.exists() and parent != parent.parent:
            parent = parent.parent
        return os.access(parent, os.W_OK | os.X_OK)

    try:
        with tempfile.NamedTemporaryFile(dir=path, delete=True) as probe:
            probe.write(b"ok")
            probe.flush()
    except OSError:
        return False
    return True


def _port_is_available(port: int) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as candidate:
            candidate.bind(("127.0.0.1", port))
    except OSError:
        return False
    return True


def _host_resolves(host: str) -> bool:
    try:
        socket.gethostbyname(host)
    except OSError:
        return False
    return True


def _permission_mode(path: Path) -> int | None:
    try:
        return stat.S_IMODE(path.stat().st_mode)
    except OSError:
        return None


def main() -> int:
    from omnibot_v3.domain.preflight import LinuxPreflightPolicy, LinuxPreflightSnapshot
    from omnibot_v3.services.linux_preflight import LinuxPreflightValidator

    args = _parse_args()

    directories = tuple(args.directories or ["data", "logs", "secrets"])
    commands = tuple(args.commands or ["bash", "systemctl", "tar"])
    ports = tuple(args.ports or [8000])
    hosts = tuple(args.hosts or ["localhost"])
    min_free_disk_bytes = int(args.min_free_gb * 1024 * 1024 * 1024)

    existing_directories = tuple(directory for directory in directories if (REPO_ROOT / directory).exists())
    default_permission_rules = LinuxPreflightPolicy().permission_rules

    policy = LinuxPreflightPolicy(
        min_free_disk_bytes=min_free_disk_bytes,
        required_commands=commands,
        required_writable_directories=directories,
        required_ports_available=ports,
        required_resolvable_hosts=hosts,
        permission_rules=(
            ()
            if args.skip_permission_checks
            else tuple(rule for rule in default_permission_rules if rule.path in existing_directories)
        ),
    )

    writable_directories = {
        directory: _is_writable_directory(REPO_ROOT / directory) for directory in directories
    }
    permission_modes = {directory: _permission_mode(REPO_ROOT / directory) for directory in directories}
    available_commands = frozenset(command for command in commands if shutil.which(command) is not None)
    port_availability = {port: _port_is_available(port) for port in ports}
    host_resolution = {host: _host_resolves(host) for host in hosts}
    free_disk_bytes = shutil.disk_usage(REPO_ROOT).free

    snapshot = LinuxPreflightSnapshot(
        platform=sys.platform,
        python_version=(sys.version_info.major, sys.version_info.minor, sys.version_info.micro),
        available_commands=available_commands,
        free_disk_bytes=free_disk_bytes,
        writable_directories=writable_directories,
        port_available=port_availability,
        resolvable_hosts=host_resolution,
        permission_modes=permission_modes,
    )

    validator = LinuxPreflightValidator(policy=policy)
    report = validator.validate(snapshot)
    print(validator.format_report(report))
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
