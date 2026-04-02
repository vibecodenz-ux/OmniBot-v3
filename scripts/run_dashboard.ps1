Param(
    [string]$BindHost = $(if ($env:OMNIBOT_BIND_HOST) { $env:OMNIBOT_BIND_HOST } else { "127.0.0.1" }),
    [int]$Port = $(if ($env:OMNIBOT_PORT) { [int]$env:OMNIBOT_PORT } else { 8000 })
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repoRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $python)) {
    throw "missing $python; run scripts/bootstrap.py or scripts/bootstrap_debian.sh first"
}

Write-Host "[run-dashboard] ensuring runtime directories exist"
& $python (Join-Path $repoRoot "scripts\init_runtime_permissions.py") `
    --root-dir $repoRoot `
    --data-root $(if ($env:OMNIBOT_DATA_ROOT) { $env:OMNIBOT_DATA_ROOT } else { "data" }) `
    --secrets-dir $(if ($env:OMNIBOT_SECRETS_DIR) { $env:OMNIBOT_SECRETS_DIR } else { "secrets" }) | Out-Null

Write-Host "[run-dashboard] ensuring React dashboard build is current"
& $python (Join-Path $repoRoot "scripts\ensure_frontend_build.py")

Write-Host "[run-dashboard] starting OmniBot dashboard on http://$BindHost`:$Port/"
Write-Host "[run-dashboard] press Ctrl+C to stop"
Set-Location $repoRoot
& $python -m uvicorn omnibot_v3.api.app:create_app --factory --host $BindHost --port $Port