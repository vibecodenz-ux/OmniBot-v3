#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_PYTHON="$REPO_ROOT/.venv/bin/python"

info() {
  printf '[run-dashboard] %s\n' "$1"
}

fail() {
  printf '[run-dashboard] ERROR: %s\n' "$1" >&2
  exit 1
}

if [[ ! -x "$VENV_PYTHON" ]]; then
  fail "missing $VENV_PYTHON; run bash scripts/bootstrap_debian.sh first"
fi

if [[ -f "$REPO_ROOT/.env" ]]; then
  while IFS= read -r raw_line || [[ -n "$raw_line" ]]; do
    line="${raw_line%$'\r'}"
    [[ -z "$line" ]] && continue
    [[ "$line" =~ ^[[:space:]]*# ]] && continue
    export "$line"
  done < "$REPO_ROOT/.env"
fi

info "ensuring runtime directories exist"
"$VENV_PYTHON" "$REPO_ROOT/scripts/init_runtime_permissions.py" \
  --root-dir "$REPO_ROOT" \
  --data-root "${OMNIBOT_DATA_ROOT:-data}" \
  --secrets-dir "${OMNIBOT_SECRETS_DIR:-secrets}" >/dev/null

info "ensuring React dashboard build is current"
"$VENV_PYTHON" "$REPO_ROOT/scripts/ensure_frontend_build.py"

HOST="${OMNIBOT_BIND_HOST:-127.0.0.1}"
PORT="${OMNIBOT_PORT:-8000}"

if [[ "$HOST" == "0.0.0.0" ]]; then
  info "local URL: http://127.0.0.1:$PORT/"
  addresses="$(hostname -I 2>/dev/null || true)"
  first_address="$(printf '%s\n' "$addresses" | awk '{print $1}')"
  if [[ -n "$first_address" ]]; then
    info "network URL: http://$first_address:$PORT/"
  fi
else
  info "dashboard URL: http://$HOST:$PORT/"
fi
info "press Ctrl+C to stop"
exec "$VENV_PYTHON" -m uvicorn omnibot_v3.api.app:create_app --factory --host "$HOST" --port "$PORT"