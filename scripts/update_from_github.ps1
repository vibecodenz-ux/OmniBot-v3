Param(
    [Parameter(Mandatory = $true)]
    [string]$RepoRoot,

    [Parameter(Mandatory = $true)]
    [string]$BackupRoot,

    [Parameter(Mandatory = $true)]
    [string]$BackupArchiveName,

    [Parameter(Mandatory = $true)]
    [string]$StateFile,

    [string]$ArchiveUrl,

    [string]$RollbackArchive,

    [string]$CurrentBuildLabel = "Unknown build",

    [string]$CurrentVersion = "unknown",

    [string]$TargetBuildLabel = "Unknown target",

    [string]$TargetVersion = "unknown",

    [string]$BindHost = "127.0.0.1",

    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"

function Read-UpdateState {
    if (-not (Test-Path -LiteralPath $StateFile)) {
        return @{}
    }

    try {
        $raw = Get-Content -LiteralPath $StateFile -Raw | ConvertFrom-Json -AsHashtable
        if ($null -eq $raw) {
            return @{}
        }
        return $raw
    } catch {
        return @{}
    }
}

function Write-UpdateState {
    Param(
        [Parameter(Mandatory = $true)]
        [hashtable]$LastAction
    )

    $parent = Split-Path -Parent $StateFile
    if ($parent) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }

    $state = Read-UpdateState
    $state["last_action"] = $LastAction
    $state | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $StateFile -Encoding UTF8
}

function Stop-DashboardProcess {
    Param(
        [int]$ListenPort
    )

    try {
        $connections = Get-NetTCPConnection -LocalPort $ListenPort -State Listen -ErrorAction Stop
        $processIds = $connections | Select-Object -ExpandProperty OwningProcess -Unique
        foreach ($processId in $processIds) {
            if ($processId) {
                Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue
            }
        }
    } catch {
        Get-Process python, python3, pwsh, powershell -ErrorAction SilentlyContinue |
            Where-Object { $_.Path -and $_.Path -like "*$RepoRoot*" } |
            Stop-Process -Force -ErrorAction SilentlyContinue
    }
}

function Copy-RepositoryChildren {
    Param(
        [Parameter(Mandatory = $true)]
        [string]$SourceRoot,

        [Parameter(Mandatory = $true)]
        [string]$DestinationRoot,

        [Parameter(Mandatory = $true)]
        [string[]]$ExcludeNames
    )

    Get-ChildItem -LiteralPath $SourceRoot -Force | ForEach-Object {
        if ($ExcludeNames -contains $_.Name) {
            return
        }

        $targetPath = Join-Path $DestinationRoot $_.Name
        Copy-Item -LiteralPath $_.FullName -Destination $targetPath -Recurse -Force
    }
}

function New-CodeBackup {
    Param(
        [Parameter(Mandatory = $true)]
        [string]$SourceRoot,

        [Parameter(Mandatory = $true)]
        [string]$BackupDirectory,

        [Parameter(Mandatory = $true)]
        [string]$ArchiveName,

        [Parameter(Mandatory = $true)]
        [string[]]$ExcludeNames,

        [Parameter(Mandatory = $true)]
        [string]$SourceBuildLabel,

        [Parameter(Mandatory = $true)]
        [string]$SourceVersion
    )

    $stageRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("omnibot-backup-stage-" + [guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Path $stageRoot | Out-Null
    New-Item -ItemType Directory -Path $BackupDirectory -Force | Out-Null

    try {
        Copy-RepositoryChildren -SourceRoot $SourceRoot -DestinationRoot $stageRoot -ExcludeNames $ExcludeNames

        $archivePath = Join-Path $BackupDirectory $ArchiveName
        if (Test-Path -LiteralPath $archivePath) {
            Remove-Item -LiteralPath $archivePath -Force
        }

        $items = Get-ChildItem -LiteralPath $stageRoot -Force
        if (-not $items) {
            throw "Backup staging produced no files."
        }

        Compress-Archive -Path (Join-Path $stageRoot '*') -DestinationPath $archivePath -Force

        $metadataPath = [System.IO.Path]::ChangeExtension($archivePath, ".json")
        @{
            archive_name = [System.IO.Path]::GetFileName($archivePath)
            created_at = [DateTime]::UtcNow.ToString("o")
            source_build_label = $SourceBuildLabel
            source_version = $SourceVersion
        } | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $metadataPath -Encoding UTF8

        return $archivePath
    } finally {
        Remove-Item -LiteralPath $stageRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}

$repoRoot = (Resolve-Path $RepoRoot).Path
$tempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("omnibot-update-" + [guid]::NewGuid().ToString("N"))
$archivePath = Join-Path $tempRoot "source.zip"
$extractRoot = Join-Path $tempRoot "extract"
$preserveNames = @(".git", ".venv", ".tools", "data", "secrets", "Put github exports here")

New-Item -ItemType Directory -Path $tempRoot | Out-Null
New-Item -ItemType Directory -Path $extractRoot | Out-Null

try {
    $isRollback = -not [string]::IsNullOrWhiteSpace($RollbackArchive)
    $isUpdate = -not $isRollback
    if ($isUpdate -and [string]::IsNullOrWhiteSpace($ArchiveUrl)) {
        throw "ArchiveUrl is required for update mode."
    }

    Write-UpdateState -LastAction @{
        action = $(if ($isRollback) { "rollback" } else { "update" })
        status = "running"
        requested_at = [DateTime]::UtcNow.ToString("o")
        current_build_label = $CurrentBuildLabel
        target_build_label = $TargetBuildLabel
        backup_archive_name = $BackupArchiveName
        rollback_archive_name = $(if ($isRollback) { [System.IO.Path]::GetFileName($RollbackArchive) } else { $null })
        message = $(if ($isRollback) { "Rollback is running." } else { "Update is running." })
    }

    Start-Sleep -Seconds 2
    Stop-DashboardProcess -ListenPort $Port

    $createdBackup = New-CodeBackup `
        -SourceRoot $repoRoot `
        -BackupDirectory $BackupRoot `
        -ArchiveName $BackupArchiveName `
        -ExcludeNames $preserveNames `
        -SourceBuildLabel $CurrentBuildLabel `
        -SourceVersion $CurrentVersion

    if ($isRollback) {
        if (-not (Test-Path -LiteralPath $RollbackArchive)) {
            throw "Rollback archive not found: $RollbackArchive"
        }

        Expand-Archive -LiteralPath $RollbackArchive -DestinationPath $extractRoot -Force
        $sourceRoot = $extractRoot
    } else {
        Invoke-WebRequest -Uri $ArchiveUrl -OutFile $archivePath -UseBasicParsing
        Expand-Archive -LiteralPath $archivePath -DestinationPath $extractRoot -Force

        $expandedRepo = Get-ChildItem -LiteralPath $extractRoot -Directory | Select-Object -First 1
        if (-not $expandedRepo) {
            throw "Downloaded update archive did not contain a repository root."
        }
        $sourceRoot = $expandedRepo.FullName
    }

    Get-ChildItem -LiteralPath $repoRoot -Force | ForEach-Object {
        if ($preserveNames -contains $_.Name) {
            return
        }

        Remove-Item -LiteralPath $_.FullName -Recurse -Force
    }

    Copy-RepositoryChildren -SourceRoot $sourceRoot -DestinationRoot $repoRoot -ExcludeNames $preserveNames

    Write-UpdateState -LastAction @{
        action = $(if ($isRollback) { "rollback" } else { "update" })
        status = "completed"
        requested_at = [DateTime]::UtcNow.ToString("o")
        completed_at = [DateTime]::UtcNow.ToString("o")
        current_build_label = $CurrentBuildLabel
        target_build_label = $TargetBuildLabel
        backup_archive_name = [System.IO.Path]::GetFileName($createdBackup)
        rollback_archive_name = $(if ($isRollback) { [System.IO.Path]::GetFileName($RollbackArchive) } else { $null })
        message = $(if ($isRollback) { "Rollback completed and OmniBot is restarting." } else { "Update completed and OmniBot is restarting." })
    }

    $runScript = Join-Path $repoRoot "scripts\run_dashboard.ps1"
    Start-Process -FilePath "powershell.exe" -WorkingDirectory $repoRoot -ArgumentList @(
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        $runScript,
        "-BindHost",
        $BindHost,
        "-Port",
        $Port
    )
} catch {
    Write-UpdateState -LastAction @{
        action = $(if (-not [string]::IsNullOrWhiteSpace($RollbackArchive)) { "rollback" } else { "update" })
        status = "failed"
        requested_at = [DateTime]::UtcNow.ToString("o")
        completed_at = [DateTime]::UtcNow.ToString("o")
        current_build_label = $CurrentBuildLabel
        target_build_label = $TargetBuildLabel
        backup_archive_name = $BackupArchiveName
        rollback_archive_name = $(if (-not [string]::IsNullOrWhiteSpace($RollbackArchive)) { [System.IO.Path]::GetFileName($RollbackArchive) } else { $null })
        message = $_.Exception.Message
    }
    throw
} finally {
    Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
}