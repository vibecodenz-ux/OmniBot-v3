# OmniBot v3

OmniBot v3 is a Linux-first, single-host-first multi-market trading platform for stocks, crypto, and forex.

Current operator-facing release: Version 3, Build 006.

This repository is intentionally separate from the v2 codebase so the current bot remains intact for reference, comparison, and rollback.

## Quick Start On Debian 13.4

Debian 13.4 is the current target for the public clone-to-run flow.

Before you run the four OmniBot commands, make sure the machine has:

- a normal user account with `sudo` rights
- `git` installed
- Node.js and npm available, or let the Debian bootstrap helper install them
- an internet connection for `apt` and Python package downloads

If your Debian account already has `sudo` and `git`, skip straight to `git clone`.

If `sudo` is missing or your user is not allowed to use it, sign in as `root` or switch to a root shell first:

```bash
su -
```

Then install the prerequisites and grant your normal login sudo access:

```bash
apt-get update
apt-get install -y sudo git
usermod -aG sudo <your-username>
exit
exit
```

Leave the root shell, fully log out of the Linux session, then sign back in as your normal user so the new `sudo` group membership applies.
Then verify the setup after you login to your user account:

```bash
sudo -v
git --version
```

Once that is done, the shortest supported path from clone to live dashboard is:

```bash
git clone <your-github-url> OmniBot-v3
cd OmniBot-v3
bash scripts/bootstrap_debian.sh
bash scripts/run_dashboard.sh
```

From the Linux machine, open `http://127.0.0.1:8000/`.

The Debian bootstrap now sets the service bind host to `0.0.0.0` on fresh installs, so another computer on the same network can also reach the dashboard without extra config.

From another computer on the same network, open `http://<your-linux-machine-ip>:8000/`.

If you rerun setup inside an older clone created before this change, delete `.env` first or update `OMNIBOT_BIND_HOST` there so the rerun picks up the LAN-capable default.

If your Linux firewall is enabled, also allow TCP port `8000` with your distro's firewall tool.

Default local development login:

- username: `admin`
- password: `admin`

What the quick start does:

1. uses `sudo` automatically when needed for Debian package installation
2. installs the minimum Debian system packages needed for a local dashboard run
3. creates `.venv`
4. installs the runtime with the API dependencies
5. installs Node.js and npm on Debian hosts when needed for the dashboard frontend build
6. creates `.env` from `.env.example` when missing
7. initializes the local runtime directories
8. builds the React dashboard frontend and starts the dashboard on port `8000`, with LAN access enabled on fresh Debian installs

If you already installed the Debian system packages yourself, use:

```bash
bash scripts/bootstrap_debian.sh --skip-system-packages
```

If update fails, run:

```bash
git pull
sudo systemctl restart omnibot-v3
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
- Linux install, upgrade, validation, systemd generation, and verification tooling
- release-readiness, API smoke, and quality-gate scripts
- automated tests and GitHub Actions CI baseline

## Initial Goals

- Linux-first installation and operations
- Single-user admin-first dashboard
- Per-market arming required before live execution
- PostgreSQL as the operational source of truth
- Service-oriented architecture with explicit runtime state transitions
- Demo or paper trading on the main runtime surface as the validation path instead of a separate strategy-testing page

## Repository Status

This is the first implementation scaffold. The repository currently includes:

- project packaging and tooling bootstrap
- initial documentation set
- API contract definitions plus a thin FastAPI runtime API scaffold for dashboard runtime reads, stored portfolio widgets, snapshot-backed analytics with provenance, explicit UI-state payloads, safe-default settings surfaces, per-market controls, audit views, credential management, command submission, and single-user session auth
- architecture decision record scaffold
- runtime domain model, worker/broker/strategy/health contracts, normalized broker portfolio reconciliation models, market worker modules, storage ports, in-memory persistence adapters, PostgreSQL runtime-store foundations with schema migration, archive, backup, data-boundary, secret-policy, and environment-configuration support, runtime health evaluation, and market-aware strategy policy overrides
- a GitHub Actions CI baseline, a local bootstrap script, a Linux deployment preflight validator, an API smoke harness plus a release-readiness validator, and shared pytest fixtures for common runtime setup
- minimal Python package entrypoint
- smoke, orchestrator transition, persistence, safety-control, broker contract, market worker, market integration, risk engine, PostgreSQL runtime store, schema migration, backup planning, data-boundary catalog, secrets policy, and runtime health tests
- a browser dashboard served by FastAPI at `/`, with per-market strategy/profile selectors, START/STOP-only module controls, a live trade journal with broker-backed manual close actions, candlestick analytics, and a single `Settings` entrypoint for API keys, runtime policy, and dashboard password changes

## Planned Structure

- `docs/` architecture, install, operations, ADRs, roadmap
- `src/omnibot_v3/` application package
- `tests/` automated tests
- `infra/` deployment and service assets
- `scripts/` local development and operational helper scripts

Current operator-focused docs include `docs/CHARTER.md` for scope and first-release definition, `docs/SERVICE_BLUEPRINT.md` for service responsibilities and deployment boundaries, `docs/BACKLOG.md` for repo-local backlog organization, `docs/INSTALLATION.md` for deployment flow, `docs/OPERATIONS.md` for day-2 operator actions, `docs/TROUBLESHOOTING.md` for failure triage and rollback, `docs/DEFERRED_ITEMS.md` for first-release boundary tracking, `docs/HARDENING.md` for Linux host security baseline guidance, and `docs/SESSION_SECURITY_REVIEW.md` for the future dashboard or API session and CSRF design baseline.

## Python Policy

- minimum supported Python: 3.11
- current primary tested development target: 3.13
- PostgreSQL support remains optional behind the `postgres` extra
- developer tooling remains isolated in the `dev` extra
- Ruff, mypy, and coverage configuration are centralized in `pyproject.toml`

## Local Quality Gate

Run `python scripts/quality_gate.py` to mirror the repo's core local CI gates: Ruff lint, mypy, and pytest with the current 90% line-coverage floor. The latest local run passed with mypy clean across 98 source files plus 256 passing tests at 91.37% coverage.

## Local Dashboard

For the simplest local run on Linux or WSL, use `bash scripts/run_dashboard.sh` after bootstrapping. On Windows PowerShell, use `powershell -ExecutionPolicy Bypass -File scripts/run_dashboard.ps1`. Both launchers refresh the React build before starting FastAPI. If you prefer the direct Python path, run `python scripts/ensure_frontend_build.py` first, then `python -m uvicorn omnibot_v3.api.app:create_app --factory --host 127.0.0.1 --port 8000`, and open `http://127.0.0.1:8000/`.

## Dashboard Frontend

The dashboard frontend lives in `frontend/` and its built output is what FastAPI serves at `/`.

The new frontend uses:

- React for component-based UI composition
- TypeScript for typed frontend state and API contracts
- Vite for a modern local development server and production build
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

See `docs/FRONTEND_MIGRATION_PLAN.md` for the remaining frontend hardening work.

In development mode, the default login is `admin` / `admin` unless `OMNIBOT_ADMIN_PASSWORD` is set. The current operator surface includes dark-theme trading modules with strategy/profile selectors, a single visible START/STOP control per market, a live trade journal with broker-backed manual CLOSE actions, and the in-app `Settings` button for API keys and dashboard password changes.

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
git clone <your-github-url> OmniBot-v3
cd OmniBot-v3
bash scripts/bootstrap_debian.sh
bash scripts/run_dashboard.sh
```

The user does not need to manually stay in a root shell for the install. The bootstrap script will call `sudo` for `apt-get` when required, but it cannot do that unless the current user already has sudo privileges.

If you want the production-style Linux deployment flow instead, use the scripts and docs in `docs/INSTALLATION.md` after the quick start succeeds.

## Release Evidence

Run `python scripts/release_evidence.py --output-file release-evidence.json` to generate a single local readiness artifact that bundles the quality gate, API smoke, and release-readiness results.

## Environment Configuration

Runtime configuration is resolved in this precedence order:

- explicit loader overrides used by tests or composition code
- process environment variables
- an opt-in plain-text `.env` file
- hardcoded development-safe defaults

Plain-text `.env` loading is disabled by default to stay aligned with the repository's secret-storage policy.

The local dashboard helper script loads `.env` explicitly when present so a Debian user can customize a cloned checkout without editing code.

## Development Principle

No dashboard route or UI component should directly mutate broker runtime internals. All runtime changes must pass through application service contracts.