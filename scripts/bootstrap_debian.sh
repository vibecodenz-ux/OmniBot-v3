#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SKIP_SYSTEM_PACKAGES=0
EXTRAS="api"
SERVICE_NAME="omnibot-v3"
SYSTEMD_OUTPUT_DIR="$REPO_ROOT/infra/generated-systemd"
SYSTEMD_ENV_FILE="/etc/omnibot/${SERVICE_NAME}.env"
SERVICE_FILE_PATH="/etc/systemd/system/${SERVICE_NAME}.service"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-system-packages)
      SKIP_SYSTEM_PACKAGES=1
      shift
      ;;
    --extras)
      EXTRAS="$2"
      shift 2
      ;;
    *)
      echo "[bootstrap-debian] ERROR: unknown argument: $1" >&2
      exit 1
      ;;
  esac
done

info() {
  printf '[bootstrap-debian] %s\n' "$1"
}

fail() {
  printf '[bootstrap-debian] ERROR: %s\n' "$1" >&2
  exit 1
}

if [[ "$(uname -s)" != "Linux" ]]; then
  fail "this helper is intended for Debian or Ubuntu on Linux"
fi

if [[ ! -f "$REPO_ROOT/pyproject.toml" ]]; then
  fail "run this script from inside the OmniBot v3 repository"
fi

APT_RUNNER=()
if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  if ! command -v sudo >/dev/null 2>&1; then
    fail "sudo is required when bootstrap_debian.sh is not run as root"
  fi
  APT_RUNNER=(sudo)
fi

PRIVILEGE_RUNNER=("${APT_RUNNER[@]}")

if [[ $SKIP_SYSTEM_PACKAGES -eq 0 ]]; then
  if ! command -v apt-get >/dev/null 2>&1; then
    fail "apt-get is required for Debian bootstrap; rerun with --skip-system-packages if your host is already prepared"
  fi
  info "installing Debian system packages required for a local dashboard run"
  "${APT_RUNNER[@]}" apt-get update
  "${APT_RUNNER[@]}" env DEBIAN_FRONTEND=noninteractive apt-get install -y \
    python3 \
    python3-venv \
    ca-certificates \
    nodejs \
    npm
fi

if [[ ! -f "$REPO_ROOT/.env" && -f "$REPO_ROOT/.env.example" ]]; then
  cp "$REPO_ROOT/.env.example" "$REPO_ROOT/.env"
  info "created .env from .env.example"
fi

info "creating the local virtual environment and installing .[$EXTRAS]"
python3 "$REPO_ROOT/scripts/bootstrap.py" --extras "$EXTRAS"

info "initializing runtime directories"
"$REPO_ROOT/.venv/bin/python" "$REPO_ROOT/scripts/init_runtime_permissions.py" --root-dir "$REPO_ROOT" >/dev/null

info "ensuring React dashboard build is current"
"$REPO_ROOT/.venv/bin/python" "$REPO_ROOT/scripts/ensure_frontend_build.py"

if ! command -v systemctl >/dev/null 2>&1; then
  fail "systemctl is required for automatic startup on boot"
fi

current_user="$(id -un)"
current_group="$(id -gn)"

info "generating systemd service assets for user $current_user"
"$REPO_ROOT/.venv/bin/python" "$REPO_ROOT/scripts/generate_systemd_units.py" \
  --service-name "$SERVICE_NAME" \
  --user "$current_user" \
  --group "$current_group" \
  --working-directory "$REPO_ROOT" \
  --python-executable "$REPO_ROOT/.venv/bin/python" \
  --environment-file "$SYSTEMD_ENV_FILE" \
  --output-dir "$SYSTEMD_OUTPUT_DIR"

info "installing systemd service and environment file"
"${PRIVILEGE_RUNNER[@]}" install -Dm644 "$SYSTEMD_OUTPUT_DIR/${SERVICE_NAME}.service" "$SERVICE_FILE_PATH"
"${PRIVILEGE_RUNNER[@]}" install -Dm640 "$REPO_ROOT/.env" "$SYSTEMD_ENV_FILE"
"${PRIVILEGE_RUNNER[@]}" systemctl daemon-reload
"${PRIVILEGE_RUNNER[@]}" systemctl enable --now "$SERVICE_NAME"

info "waiting for the dashboard to answer on localhost"
for attempt in {1..30}; do
  if "$REPO_ROOT/.venv/bin/python" - <<'PY'
import urllib.request

with urllib.request.urlopen('http://127.0.0.1:8000/', timeout=5) as response:
    raise SystemExit(0 if response.status == 200 else 1)
PY
  then
    break
  fi
  if [[ "$attempt" -eq 30 ]]; then
    "${PRIVILEGE_RUNNER[@]}" journalctl -u "$SERVICE_NAME" -n 50 --no-pager || true
    fail "systemd service started but the dashboard did not become ready"
  fi
  sleep 2
done

bind_host="$(grep '^OMNIBOT_BIND_HOST=' "$REPO_ROOT/.env" | tail -n 1 | cut -d '=' -f 2- || true)"
port="$(grep '^OMNIBOT_PORT=' "$REPO_ROOT/.env" | tail -n 1 | cut -d '=' -f 2- || true)"
bind_host="${bind_host:-0.0.0.0}"
port="${port:-8000}"

if [[ "$bind_host" == "0.0.0.0" ]]; then
  info "local URL: http://127.0.0.1:$port/"
  addresses="$(hostname -I 2>/dev/null || true)"
  first_address="$(printf '%s\n' "$addresses" | awk '{print $1}')"
  if [[ -n "$first_address" ]]; then
    info "network URL: http://$first_address:$port/"
  fi
else
  info "dashboard URL: http://$bind_host:$port/"
fi

info "local dashboard bootstrap complete"
info "systemd service $SERVICE_NAME is enabled and will start automatically after reboot"
info "manual foreground run remains available via: bash scripts/run_dashboard.sh"
info "default development login: admin / admin"