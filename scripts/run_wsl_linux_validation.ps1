param(
    [string]$Distro,
    [ValidateSet('full', 'install', 'upgrade')]
    [string]$Phase = 'full',
    [switch]$Execute,
    [ValidateSet('text', 'json')]
    [string]$Format = 'text',
    [string]$Distribution = 'ubuntu-24.04',
    [string]$PythonExecutable = 'python3',
    [string]$ServiceName = 'omnibot-v3',
    [string]$UserName,
    [string]$GroupName,
    [string]$EnvironmentFile = '/etc/omnibot/omnibot-v3.env',
    [string]$BackupDir = '/var/backups/omnibot',
    [string]$DatabaseUrl = 'postgresql://omnibot:change-me@localhost:5432/omnibot',
    [string]$DataRoot = 'data',
    [string]$SecretsDir = 'secrets',
    [string]$ConstraintsFile = 'requirements/linux-postgres-constraints.txt',
    [string]$Extras = 'postgres',
    [string]$OutputFile
)

$ErrorActionPreference = 'Stop'

function Get-InstalledWslDistros {
    $output = & wsl.exe -l -q 2>$null
    if ($LASTEXITCODE -ne 0) {
        return @()
    }

    return @(
        $output |
            ForEach-Object { $_.Trim() } |
            Where-Object { $_ }
    )
}

$installedDistros = Get-InstalledWslDistros
if ($installedDistros.Count -eq 0) {
    Write-Error @'
No WSL distributions are installed.

Install one first, for example:
  wsl.exe --list --online
  wsl.exe --install Ubuntu-24.04

After the distro is installed and initialized, re-run this helper.
'@
}

if (-not $Distro) {
    $Distro = $installedDistros[0]
}

if ($installedDistros -notcontains $Distro) {
    $available = $installedDistros -join ', '
    Write-Error "WSL distro '$Distro' is not installed. Installed distros: $available"
}

$repoRootWindows = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$repoRootLinux = (& wsl.exe -d $Distro -- bash -lc "wslpath -a '$repoRootWindows'").Trim()
if ($LASTEXITCODE -ne 0 -or -not $repoRootLinux) {
    Write-Error "Failed to translate repo path '$repoRootWindows' into the WSL distro '$Distro'."
}

$linuxUser = if ($UserName) { $UserName } else { (& wsl.exe -d $Distro -- bash -lc 'id -un').Trim() }
$linuxGroup = if ($GroupName) { $GroupName } else { (& wsl.exe -d $Distro -- bash -lc 'id -gn').Trim() }

$arguments = @(
    'scripts/validate_linux_vm.py'
    '--distribution', $Distribution
    '--phase', $Phase
    '--service-name', $ServiceName
    '--user', $linuxUser
    '--group', $linuxGroup
    '--working-directory', $repoRootLinux
    '--python-executable', $PythonExecutable
    '--environment-file', $EnvironmentFile
    '--backup-dir', $BackupDir
    '--database-url', $DatabaseUrl
    '--data-root', $DataRoot
    '--secrets-dir', $SecretsDir
    '--constraints-file', $ConstraintsFile
    '--extras', $Extras
    '--format', $Format
)

if ($Execute) {
    $arguments += '--execute'
}

if ($OutputFile) {
    $arguments += @('--output-file', $OutputFile)
}

$quotedArgs = $arguments | ForEach-Object {
    "'" + ($_ -replace "'", "'\''") + "'"
}
$command = @(
    'cd', "'$repoRootLinux'", ';',
    $PythonExecutable
    $quotedArgs
) -join ' '

Write-Host "Running Linux validation in WSL distro '$Distro' from $repoRootLinux"
& wsl.exe -d $Distro -- bash -lc $command
exit $LASTEXITCODE