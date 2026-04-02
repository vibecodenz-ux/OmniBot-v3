# OmniBot v3

OmniBot v3 is a Linux-first, single-host-first multi-market trading platform for stocks, crypto, and forex.

## Quick Start On Debian 13.4

This package is set up for Debian 13.4.

Run OmniBot as a normal Linux user. If that user already has `sudo` and `git`, skip to the clone section.

If `sudo` is missing or your user is not in the `sudo` group, switch to a root shell first:

```bash
su -
```

Then run these commands exactly, replacing `<your-username>` with your normal Linux login name:

```bash
apt-get update
apt-get install -y sudo git
usermod -aG sudo <your-username>
```

Leave the root shell, sign back in as your normal user, and verify the setup:

```bash
exit
sudo -v
git --version
```

After that, sign in as your normal user and run these commands exactly:

```bash
git clone https://github.com/vibecodenz-ux/OmniBot-v3 OmniBot-v3
cd OmniBot-v3
bash scripts/bootstrap_debian.sh
```

What `bootstrap_debian.sh` does for you:

- installs Python, venv support, Node.js, and npm when needed
- creates `.venv`
- installs the OmniBot runtime
- creates `.env` from `.env.example` when missing
- initializes runtime directories
- builds the dashboard frontend
- installs and enables a `systemd` service named `omnibot-v3`
- starts the service immediately
- configures OmniBot to start automatically again after a reboot or power loss

What you should see:

- a local URL like `http://127.0.0.1:8000/`
- a network URL like `http://192.168.x.x:8000/`

Open the network URL from another PC on the same LAN.

Default login:

- username: `admin`
- password: `admin`

Notes:

- this package is currently documented for Debian 13.4
- the quick-start flow does not depend on a fixed Linux username or home path; run it from your own cloned working tree
- the dashboard binds to `0.0.0.0` by default so the printed network URL works on a real Linux host
- the installer enables `omnibot-v3` in `systemd`, so the dashboard comes back automatically after reboot with no terminal interaction
- if port `8000` is already in use, edit `.env` and change `OMNIBOT_PORT`

If you already installed the Debian system packages yourself, use:

```bash
bash scripts/bootstrap_debian.sh --skip-system-packages
```

Optional commands after install:

```bash
sudo systemctl status omnibot-v3 --no-pager
sudo systemctl restart omnibot-v3
```

If the service does not come up cleanly, inspect the logs with:

```bash
sudo journalctl -u omnibot-v3 -n 100 --no-pager
```

## What You Get

The current dashboard shell is the React frontend served by FastAPI at `/` and includes:

- per-market strategy selectors
- per-market profile selectors
- START or STOP module controls
- a live trade journal with broker-backed manual close actions
- a Settings flow for runtime policy, API keys, and dashboard password changes

Selections and dashboard password changes persist in `data/operator-state.json` by default.

Historical market-data warmup now persists normalized bars in `data/historical-bars.json` by default. The scanner uses that cache to preload candle history for supported brokers, rank symbols before evaluation, and drive the per-market candlestick analytics cards in the dashboard.

## Repository Status

The repository currently includes:

- a FastAPI-served React dashboard and API surface
- an in-memory orchestrator with explicit market state transitions and safety controls
- mock-backed broker and market worker foundations for stocks, crypto, and forex
- risk-policy and strategy metadata foundations
- PostgreSQL runtime-store, migration, backup, and restore-planning foundations
- Linux bootstrap, systemd generation, runtime permission, and dashboard launch tooling

## Initial Goals

- Linux-first installation and operations
- Single-user admin-first dashboard
- Per-market arming required before live execution
- PostgreSQL as the operational source of truth
- Service-oriented architecture with explicit runtime state transitions
- Demo or paper trading on the main runtime surface as the validation path instead of a separate strategy-testing page

## Repository Status

The public export currently includes:

- project packaging and tooling bootstrap
- API contract definitions plus a thin FastAPI runtime API scaffold for dashboard runtime reads, stored portfolio widgets, snapshot-backed analytics with provenance, explicit UI-state payloads, safe-default settings surfaces, per-market controls, audit views, credential management, command submission, and single-user session auth
- runtime domain model, worker/broker/strategy/health contracts, normalized broker portfolio reconciliation models, market worker modules, storage ports, in-memory persistence adapters, PostgreSQL runtime-store foundations with schema migration, archive, backup, data-boundary, secret-policy, and environment-configuration support, runtime health evaluation, and market-aware strategy policy overrides
- a local bootstrap script plus the runtime helper scripts needed for install, service setup, and dashboard launch
- minimal Python package entrypoint
- a browser dashboard served by FastAPI at `/`, with per-market strategy/profile selectors, START/STOP-only module controls, a live trade journal with broker-backed manual close actions, candlestick analytics, and a single `Settings` entrypoint for API keys, runtime policy, and dashboard password changes

## Planned Structure

- `src/omnibot_v3/` application package
- `requirements/` operational constraint files used by install and upgrade tooling
- `scripts/` install and runtime helper scripts

This export keeps the public user guidance in this top-level README instead of shipping a separate docs set.

## Python Policy

- minimum supported Python: 3.11
- PostgreSQL support remains optional behind the `postgres` extra

## Local Dashboard

For the simplest local run on Linux or WSL, use `bash scripts/run_dashboard.sh` after bootstrapping. On Windows PowerShell, use `powershell -ExecutionPolicy Bypass -File scripts/run_dashboard.ps1`. Both launchers refresh the React build before starting FastAPI. If you prefer the direct Python path, run `python scripts/ensure_frontend_build.py` first, then `python -m uvicorn omnibot_v3.api.app:create_app --factory --host 127.0.0.1 --port 8000`, and open `http://127.0.0.1:8000/`.

## Dashboard Frontend

The dashboard frontend lives in `frontend/` and its built output is what FastAPI serves at `/`.

The new frontend uses:

- React for component-based UI composition
- TypeScript for typed frontend state and API contracts
- Vite for frontend bundling and production build
- TanStack Query for API fetching, cache invalidation, and refresh behavior

Current frontend status:

1. the new app is scaffolded in `frontend/`
2. it already authenticates against `/v1/auth/login`, `/v1/auth/session`, and `/v1/auth/logout`
3. it consumes the existing `/v1/dashboard` bundle and module command endpoints
4. FastAPI serves the built React app as the only dashboard shell

Expected local workflow once Node.js is installed:

```bash
cd frontend
npm install
npm run dev
```

By default, the Vite dev server on `http://127.0.0.1:5173/` proxies `/v1/*` requests to `http://127.0.0.1:8000`, so the FastAPI API should be running locally at the same time. The UI served at `http://127.0.0.1:8000/` is the same dashboard family, using the built frontend assets.

The default login is `admin` / `admin` unless `OMNIBOT_ADMIN_PASSWORD` is set. The current operator surface includes dark-theme trading modules with strategy/profile selectors, a single visible START/STOP control per market, a live trade journal with broker-backed manual CLOSE actions, and the in-app `Settings` button for API keys and dashboard password changes.

The analytics view now also includes per-market scanner insight cards with warmup status, ranked symbols, and selectable candlestick charts sourced from the historical bar cache when the broker supports it.

For local overrides, copy `.env.example` to `.env` and adjust values as needed.

Dashboard bind settings supported by the repo:

- `OMNIBOT_BIND_HOST` controls the host to bind to, default `127.0.0.1`
- `OMNIBOT_PORT` controls the port, default `8000`

## WSL LAN Access

If you are running the dashboard inside WSL Debian and want to reach it from another PC on your network, there are two separate pieces:

1. the dashboard must bind to a LAN-friendly host such as `0.0.0.0`
2. Windows must forward the port from the Windows host to the current WSL IP and allow it through the firewall

The repo now supports that with a Windows helper:

```powershell
PowerShell -ExecutionPolicy Bypass -File scripts/publish_wsl_dashboard.ps1 -Distro Debian -Port 8000 -UpdateEnv
```

What it does:

1. updates your local `.env` with `OMNIBOT_BIND_HOST=0.0.0.0` and the chosen port
2. creates or refreshes the Windows firewall rule
3. updates the Windows `portproxy` mapping to the current WSL IP
4. prints the Windows LAN URLs you can use from another PC

Important limitation:

The repo-side support only needs to be added once, and that is now done. The Windows forwarding helper should be rerun after a Debian reinstall or any time the WSL IP changes, because `portproxy` must target the current WSL address.

## Debian Clone To Run

If you want the dashboard running from a plain GitHub clone without the full systemd deployment flow, this is the supported path on Debian 13.4 after `git` and `sudo` are ready on the machine:

```bash
git clone https://github.com/vibecodenz-ux/OmniBot-v3 OmniBot-v3
cd OmniBot-v3
bash scripts/bootstrap_debian.sh
bash scripts/run_dashboard.sh
```

The user does not need to manually stay in a root shell for the install. The bootstrap script will call `sudo` for `apt-get` when required, but it cannot do that unless the current user already has sudo privileges.

If you want a more manual deployment flow after the quick start succeeds, use the scripts in `scripts/` directly from the cloned working tree.

## Environment Configuration

Runtime configuration is resolved in this precedence order:

- explicit loader overrides used by tests or composition code
- process environment variables
- an opt-in plain-text `.env` file
- hardcoded safe defaults

Plain-text `.env` loading is disabled by default to stay aligned with the repository's secret-storage policy.

The local dashboard helper script loads `.env` explicitly when present so a Debian user can customize a cloned checkout without editing code.

## Runtime Principle

No dashboard route or UI component should directly mutate broker runtime internals. All runtime changes must pass through application service contracts.