# OmniBot v3

OmniBot v3 is a Linux-first, single-host-first trading dashboard for stocks, crypto, and forex.

## Quick Start On Debian 13

This package is prepared for Debian 13 on either:

- a normal Linux machine
- Debian under WSL2 with systemd enabled

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

After that, run these commands exactly:

```bash
git clone https://github.com/vibecodenz-ux/OmniBot-v3.git OmniBot-v3
cd OmniBot-v3
bash scripts/bootstrap_debian.sh
```

If you are testing in WSL, clone into a Linux-native path such as `~/OmniBot-v3`, not a Windows-mounted path under `/mnt/c/...`.

What `bootstrap_debian.sh` does for you:

- installs Python, venv support, Node.js, and npm when needed
- creates `.venv`
- installs the OmniBot runtime with API dependencies
- creates `.env` from `.env.example` when missing
- initializes runtime directories
- builds the React dashboard frontend
- generates and installs the `systemd` service assets
- enables and starts the `omnibot-v3` service automatically
- waits for a successful local HTTP `200` readiness result before reporting success

What you should see after install:

- a local URL: `http://127.0.0.1:8000/`
- if you later change `OMNIBOT_BIND_HOST=0.0.0.0` in `.env`, the script output may also print a LAN URL

Default login:

- username: `admin`
- password: `admin`

If you already installed the Debian system packages yourself, use:

```bash
bash scripts/bootstrap_debian.sh --skip-system-packages
```

Useful follow-up commands:

```bash
sudo systemctl status omnibot-v3 --no-pager
sudo systemctl restart omnibot-v3
sudo journalctl -u omnibot-v3 -n 100 --no-pager
```

For a manual foreground run after bootstrap, use:

```bash
bash scripts/run_dashboard.sh
```

## What You Get

This export includes:

- the FastAPI runtime and React dashboard
- the Debian bootstrap and dashboard launch scripts
- the updater handoff script used by the in-app updater
- the frontend source and package metadata needed to rebuild `frontend/dist`
- the runtime source tree under `src/omnibot_v3`
- `.env.example` and `pyproject.toml`

The dashboard currently includes:

- per-market controls and strategy selection
- analytics and candlestick views
- a trade journal
- a Settings page with broker credential management
- build display plus GitHub update, backup, and rollback controls

## WSL Note

For WSL rehearsal on Windows:

- use Debian 13 or another current Debian/Ubuntu distro with systemd enabled
- clone into your Linux home directory
- access the dashboard locally at `http://127.0.0.1:8000/`
- if you need LAN access from Windows or another PC, use `scripts/publish_wsl_dashboard.ps1` from Windows PowerShell

## Environment

Copy `.env.example` to `.env` if you want to override defaults.

Common settings:

- `OMNIBOT_BIND_HOST`, default `127.0.0.1`
- `OMNIBOT_PORT`, default `8000`
- `OMNIBOT_DATA_ROOT`, default `data`
- `OMNIBOT_SECRETS_DIR`, default `secrets`
- `OMNIBOT_UPDATE_REPO`, optional override for the in-app GitHub updater source
- `OMNIBOT_UPDATE_BRANCH`, optional override for the in-app GitHub updater branch
