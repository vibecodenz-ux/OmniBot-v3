# Scripts

Developer and operational helper scripts for OmniBot v3.

## bootstrap_debian.sh

Installs the minimum Debian system packages for a local dashboard run, creates `.venv`, installs the runtime, initializes local directories, and prints the next launch step:

```
bash scripts/bootstrap_debian.sh
bash scripts/bootstrap_debian.sh --skip-system-packages
bash scripts/bootstrap_debian.sh --extras api,postgres
```

This is the main quickstart helper for a GitHub clone on Debian.

## run_dashboard.sh

Loads `.env` if present, ensures runtime directories exist, refreshes the React dashboard build when needed, and starts the local dashboard:

```
bash scripts/run_dashboard.sh
```

It runs the app on `http://127.0.0.1:8000/` using the repository-local `.venv`.

On first run it will install missing frontend dependencies under `frontend/node_modules`, then build `frontend/dist` before launch.

It also respects these environment variables:

1. `OMNIBOT_BIND_HOST`
2. `OMNIBOT_PORT`

## run_dashboard.ps1

Windows-friendly launcher that mirrors the Bash helper: it ensures runtime directories exist, refreshes the React dashboard build, and starts FastAPI on the configured host and port.

```powershell
powershell -ExecutionPolicy Bypass -File scripts/run_dashboard.ps1
```

It respects the same environment variables as the Bash helper:

1. `OMNIBOT_BIND_HOST`
2. `OMNIBOT_PORT`

## publish_wsl_dashboard.ps1

Publishes the WSL-hosted dashboard to the Windows host and local network for testing from another PC:

```powershell
PowerShell -ExecutionPolicy Bypass -File scripts/publish_wsl_dashboard.ps1 -Distro Debian -Port 8000 -UpdateEnv
```

This helper is intended for local WSL testing on Windows, not for a normal Linux deployment. It requires an elevated PowerShell session because it updates the Windows firewall and `netsh interface portproxy` rules.

## bootstrap.py

Sets up the local development environment from scratch:

```
python scripts/bootstrap.py                  # creates .venv, installs dev+postgres extras
python scripts/bootstrap.py --extras dev     # install dev extras only
python scripts/bootstrap.py --extras api --skip-tool-preflight
python scripts/bootstrap.py --skip-venv      # use current interpreter, no venv creation
```

Steps performed:
1. Checks Python ≥ 3.11.
2. Creates `.venv/` with `python -m venv` (skipped if it already exists).
3. Installs the project with `pip install -e ".[<extras>]"`.
4. Runs a preflight that always verifies `omnibot_v3` is importable and optionally verifies `ruff`, `mypy`, and `pytest`.

## linux_preflight.py

Runs Linux deployment checks before install or upgrade:

```
python scripts/linux_preflight.py
python scripts/linux_preflight.py --directory runtime --port 8000 --command pg_dump --host db.internal
```

Checks currently covered:
1. Linux platform
2. Python version minimum
3. required commands in `PATH`
4. free disk space
5. writable runtime directories
6. required local port availability
7. hostname resolution
8. permission modes for runtime directories

## generate_systemd_units.py

Generates systemd deployment assets using actual user, group, path, and interpreter values:

```
python scripts/generate_systemd_units.py
python scripts/generate_systemd_units.py --user omnibot --group omnibot --working-directory /opt/omnibot
```

Outputs currently generated:
1. `<service>.service` unit file
2. `<service>.env` environment template
3. suggested `install` and `systemctl` commands for deployment

## run_backup.py

Runs a PostgreSQL backup and writes a manifest JSON file:

```
python scripts/run_backup.py --database-url postgresql://omnibot:secret@localhost:5432/omnibot --output-dir /var/backups/omnibot
python scripts/run_backup.py --database-url postgresql://omnibot:secret@localhost:5432/omnibot --output-dir /var/backups/omnibot --plan-only
```

## restore_validation_report.py

Emits a JSON restore-validation report from an existing dump:

```
python scripts/restore_validation_report.py --database-url postgresql://omnibot:secret@localhost:5432/omnibot --backup-file /var/backups/omnibot/omnibot-20260330T120000Z.dump
python scripts/restore_validation_report.py --database-url postgresql://omnibot:secret@localhost:5432/omnibot --backup-file /var/backups/omnibot/omnibot-20260330T120000Z.dump --output-file restore-report.json
```

## runtime_probe.py

Runs health or readiness probes with supervisor-friendly exit codes:

```
python scripts/runtime_probe.py --mode health
python scripts/runtime_probe.py --mode readiness --validate-workers --reconcile-workers --connect-markets
```

Defaults to JSON output. Use `--format text` for a line-oriented summary.

## api_smoke.py

Runs an in-process authenticated API smoke flow over the FastAPI app and returns a release-readiness friendly exit code:

```
python scripts/api_smoke.py
python scripts/api_smoke.py --format text
```

The smoke flow currently checks:
1. unauthenticated runtime access is rejected
2. login and session views succeed
3. runtime, health, settings, portfolio, analytics, and UI-state reads succeed
4. CSRF-protected market validation, market connection, reconciliation, and logout succeed
5. reconciliation produces a stored snapshot that is visible through portfolio and analytics reads

## release_readiness.py

Runs an in-process release-readiness validation over the current runtime, broker, recovery, and backup foundations:

```
python scripts/release_readiness.py
python scripts/release_readiness.py --format text
```

The release-readiness flow currently checks:
1. all default workers validate cleanly and require explicit market arming
2. disconnected markets cannot arm, and the connect-arm-start-stop-disarm flow behaves correctly
3. portfolio totals and analytics remain snapshot-backed with matching values and provenance
4. persisted running markets are automatically recovered to a safe armed state on startup
5. backup and restore planning includes both schemas and post-restore verification queries
4. runs the extracted result through the existing dry-run migration validator and can emit the normalized bundle directly with `--bundle-only`

## quality_gate.py

Runs the core local quality gates that mirror the main CI verification path:

```
python scripts/quality_gate.py
python scripts/quality_gate.py --format text
python scripts/quality_gate.py --coverage-xml coverage.xml
```

The quality gate currently runs:
1. `ruff check .`
2. `mypy`
3. `pytest` with line coverage over `src/omnibot_v3` and the repo-wide `fail_under = 90` threshold

## release_evidence.py

Generates a consolidated local release evidence artifact from the current repo state:

```
python scripts/release_evidence.py
python scripts/release_evidence.py --format text
python scripts/release_evidence.py --output-file release-evidence.json
```

The evidence artifact currently bundles:
1. the local quality gate report
2. the API smoke report
3. the release-readiness report
4. basic local environment metadata such as platform, Python version, and coverage artifact path

## init_runtime_permissions.py

Creates runtime directories and applies secure permissions:

```
python scripts/init_runtime_permissions.py --plan-only
python scripts/init_runtime_permissions.py --root-dir /opt/omnibot
python scripts/init_runtime_permissions.py --root-dir /opt/omnibot --data-root var/data --secrets-dir var/secrets
```

## install_linux.py

Builds or executes the composed Linux install flow:

```
python scripts/install_linux.py
python scripts/install_linux.py --service-name omnibot-v3 --user omnibot --group omnibot --working-directory /opt/omnibot
python scripts/install_linux.py --execute --service-name omnibot-v3 --user omnibot --group omnibot --working-directory /opt/omnibot
```

The installer creates or refreshes a local `.venv` under the target working directory and then uses `requirements/linux-postgres-constraints.txt` to pin the operational PostgreSQL dependency set during `pip install`.

## upgrade_linux.py

Builds or executes the composed Linux upgrade flow:

```
python scripts/upgrade_linux.py
python scripts/upgrade_linux.py --service-name omnibot-v3 --user omnibot --group omnibot --working-directory /opt/omnibot
python scripts/upgrade_linux.py --execute --service-name omnibot-v3 --user omnibot --group omnibot --working-directory /opt/omnibot
```

The upgrade flow takes a pre-upgrade PostgreSQL backup, refreshes the local `.venv`, reapplies the pinned dependency set, and regenerates deployment assets.

## validate_linux_vm.py

Builds or executes the clean Debian/Ubuntu VM validation flow:

```
python scripts/validate_linux_vm.py
python scripts/validate_linux_vm.py --distribution debian-12 --phase install
python scripts/validate_linux_vm.py --distribution ubuntu-24.04 --execute --output-file vm-validation-report.txt
```

The validation harness reuses the composed install and upgrade plans and emits either a plan or an execution report with manual follow-up checks for supervised service readiness and rollback evidence retention.

When `--backup-dir` is not provided, the validation harness defaults to a user-writable backup path under the working tree: `.artifacts/backups`. This keeps Debian or Ubuntu rehearsal runs usable for non-root validation while leaving the operational install and upgrade entrypoints free to target `/var/backups/omnibot` or another host-level backup path.

## run_wsl_linux_validation.ps1

Launches the Linux validation harness from Windows into an installed WSL distro:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/run_wsl_linux_validation.ps1
powershell -ExecutionPolicy Bypass -File scripts/run_wsl_linux_validation.ps1 -Distro Ubuntu-24.04 -Phase install
powershell -ExecutionPolicy Bypass -File scripts/run_wsl_linux_validation.ps1 -Distro Debian -Execute -OutputFile vm-validation-report.txt
```

The helper currently:
1. checks that at least one WSL distro is installed
2. translates the repo path from Windows into the target distro path
3. resolves the Linux user and group from the target distro unless they are provided explicitly
4. runs `scripts/validate_linux_vm.py` inside the chosen distro with the Linux path and arguments already mapped

If WSL itself is installed but no distro exists yet, install one first with `wsl.exe --list --online` and `wsl.exe --install <Distro>`.

## Manual validation commands

```
python -m ruff check .
python -m ruff format --check .
python -m mypy src tests
python -m pytest -ra
```

## Notes

The backup and restore command-planning foundation lives in
[src/omnibot_v3/infra/backup_restore.py](../src/omnibot_v3/infra/backup_restore.py)
until the Linux-first operational scripts are generated in Milestone 11.