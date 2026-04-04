# OmniBot v3

OmniBot v3 is a Linux-first, single-host-first trading dashboard for stocks, crypto, and forex.

Current operator-facing release: Version 3, Build 007.

## Quick Start On Debian 13

This package is prepared for Debian 13.

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
exit
exit
```

Leave the root shell, fully log out of the Linux session, then sign back in as your normal user so the new `sudo` group membership applies.
Then verify the setup after you login to your user account:

```bash
sudo -v
git --version
```

After that, run these commands exactly:

```bash
git clone https://github.com/vibecodenz-ux/OmniBot-v3.git OmniBot-v3
cd OmniBot-v3
bash scripts/bootstrap_debian.sh
```

What `bootstrap_debian.sh` does for you:

- installs Python, venv support, Node.js, and npm when needed
- creates `.venv`
- installs the OmniBot runtime with API dependencies
- creates `.env` from `.env.example` when missing
- initializes runtime directories
- builds the React dashboard frontend locally
- generates and installs the `systemd` service assets
- enables and starts the `omnibot-v3` service automatically
- waits for a successful local HTTP `200` readiness result before reporting success

What you should see after install:

- a local URL: `http://127.0.0.1:8000/`
- on a LAN-connected machine, a network URL such as `http://<your-linux-machine-ip>:8000/`

Fresh installs default to `OMNIBOT_BIND_HOST=0.0.0.0`, so another computer on the same network can reach the dashboard without extra config.

If your Linux firewall is enabled, also allow TCP port `8000` with your distro's firewall tool.

Default login:

- username: `admin`
- password: `admin`

If you already installed the Debian system packages yourself, use:

```bash
bash scripts/bootstrap_debian.sh --skip-system-packages
```

If update fails, run:

```bash
git pull
sudo systemctl restart omnibot-v3
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
- the Debian bootstrap, local launcher, and updater scripts needed to install, run, and update the package
- the frontend source and package metadata needed to rebuild `frontend/dist`
- the runtime source tree under `src/omnibot_v3`
- `.env.example`, `.gitignore`, `pyproject.toml`, and `requirements/`

The dashboard currently includes:

- per-market profile selectors and START or STOP module controls
- autonomous scanner and strategy-family summaries
- analytics and candlestick views
- a trade journal
- a Settings page with broker credential management
- build display plus GitHub update, backup, and rollback controls

## Environment

Copy `.env.example` to `.env` if you want to override defaults.

Common settings:

- `OMNIBOT_BIND_HOST`, default `0.0.0.0`
- `OMNIBOT_PORT`, default `8000`
- `OMNIBOT_DATA_ROOT`, default `data`
- `OMNIBOT_SECRETS_DIR`, default `secrets`
- `OMNIBOT_UPDATE_REPO`, optional override for the in-app GitHub updater source
- `OMNIBOT_UPDATE_BRANCH`, optional override for the in-app GitHub updater branch