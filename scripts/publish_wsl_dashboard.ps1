param(
    [string]$Distro = 'Debian',
    [int]$Port = 8000,
    [string]$ListenAddress = '0.0.0.0',
    [switch]$UpdateEnv
)

$ErrorActionPreference = 'Stop'

function Test-IsAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)
}

if (-not (Test-IsAdministrator)) {
    Write-Error 'Run this script from an elevated PowerShell session so it can update Windows firewall and portproxy settings.'
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$envPath = Join-Path $repoRoot '.env'
$envExamplePath = Join-Path $repoRoot '.env.example'

if ($UpdateEnv) {
    if (-not (Test-Path $envPath)) {
        if (-not (Test-Path $envExamplePath)) {
            Write-Error "Could not find $envExamplePath"
        }
        Copy-Item $envExamplePath $envPath
    }

    $lines = if (Test-Path $envPath) { Get-Content $envPath } else { @() }
    $updated = @()
    $hostWritten = $false
    $portWritten = $false
    foreach ($line in $lines) {
        if ($line -match '^OMNIBOT_BIND_HOST=') {
            $updated += 'OMNIBOT_BIND_HOST=0.0.0.0'
            $hostWritten = $true
            continue
        }
        if ($line -match '^OMNIBOT_PORT=') {
            $updated += "OMNIBOT_PORT=$Port"
            $portWritten = $true
            continue
        }
        $updated += $line
    }
    if (-not $hostWritten) {
        $updated += 'OMNIBOT_BIND_HOST=0.0.0.0'
    }
    if (-not $portWritten) {
        $updated += "OMNIBOT_PORT=$Port"
    }
    Set-Content -Path $envPath -Value $updated
}

$wslAddresses = (& wsl.exe -d $Distro -- hostname -I).Trim()
if (-not $wslAddresses) {
    Write-Error "Could not determine the IP address for WSL distro '$Distro'."
}
$wslIp = ($wslAddresses -split '\s+')[0]

$ruleName = "OmniBot-v3 WSL Dashboard $Port"
if (-not (Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -Action Allow -Protocol TCP -LocalPort $Port | Out-Null
}

& netsh interface portproxy delete v4tov4 listenport=$Port listenaddress=$ListenAddress | Out-Null
& netsh interface portproxy add v4tov4 listenport=$Port listenaddress=$ListenAddress connectport=$Port connectaddress=$wslIp | Out-Null

$lanIps = @(
    Get-NetIPConfiguration |
        Where-Object { $_.IPv4DefaultGateway -ne $null -and $_.NetAdapter.Status -eq 'Up' } |
        ForEach-Object { $_.IPv4Address.IPAddress } |
        Where-Object { $_ -and $_ -ne '127.0.0.1' }
)

Write-Host "WSL dashboard publishing updated."
Write-Host "WSL distro: $Distro"
Write-Host "WSL IP: $wslIp"
Write-Host "Listen port: $Port"
if ($UpdateEnv) {
    Write-Host "Updated repo .env for LAN binding: $envPath"
}
if ($lanIps.Count -gt 0) {
    Write-Host 'Reach the dashboard from another PC using one of:'
    foreach ($ip in $lanIps) {
        Write-Host "  http://$ip`:$Port/"
    }
} else {
    Write-Host 'No LAN IPv4 address with a default gateway was detected on Windows.'
}
Write-Host 'Re-run this helper after a Debian reinstall or any time the WSL IP changes.'