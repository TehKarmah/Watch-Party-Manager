<#
.SYNOPSIS
    Developer-only utility: permanently deletes local WASH runtime data so
    the next bot startup behaves like a fresh installation.

.DESCRIPTION
    Deletes the known runtime JSON files and migration ".bak" artifacts
    directly under data/, and clears everything inside data/backups/,
    while preserving both the data/ and data/backups/ directories
    themselves. Never touches source-controlled files (documentation,
    templates, .gitkeep) and never runs without typed confirmation.

    Intended for repeated fresh-install / /setup testing during local
    development. Not part of end-user installation.

.EXAMPLE
    .\scripts\reset_dev_data.ps1
#>

[CmdletBinding()]
param()

Set-StrictMode -Version Latest

# Resolve paths relative to this script's own location so it works from
# any working directory.
$repoRoot = Split-Path -Parent $PSScriptRoot
$dataDirectory = Join-Path $repoRoot 'data'
$backupsDirectory = Join-Path $dataDirectory 'backups'

# Files intentionally kept even when clearing data/backups/ -- these are
# source-controlled placeholders, not runtime data.
$preservedBackupFileNames = @('.gitkeep', 'README.md')

$knownRuntimeFileNames = @(
    'guild_configurations.json',
    'rotations.json',
    'scheduled_jobs.json',
    'setup_wizard_state.json',
    'suggestion_database_configurations.json',
    'suggestion_database_configurations.json.pre_migration.bak',
    'suggestion_databases.json',
    'suggestions.json',
    'voting.json'
)

Write-Host ''
Write-Host '=================================================================' -ForegroundColor Yellow
Write-Host ' WASH Local Data Reset (developer utility)' -ForegroundColor Yellow
Write-Host '=================================================================' -ForegroundColor Yellow
Write-Host ''
Write-Host 'This will PERMANENTLY DELETE local WASH runtime data under:' -ForegroundColor Yellow
Write-Host "  $dataDirectory" -ForegroundColor Yellow
Write-Host ''
Write-Host 'That includes guild configuration, suggestions, suggestion' -ForegroundColor Yellow
Write-Host 'databases, votes, rotations, scheduled jobs, setup wizard state,' -ForegroundColor Yellow
Write-Host 'migration backup artifacts, and everything inside data/backups/.' -ForegroundColor Yellow
Write-Host 'This cannot be undone.' -ForegroundColor Yellow
Write-Host ''

$confirmation = Read-Host 'Type RESET to continue, or anything else to cancel'

if (-not [string]::Equals($confirmation, 'RESET', [System.StringComparison]::Ordinal)) {
    Write-Host ''
    Write-Host 'Cancelled. No files were deleted.' -ForegroundColor Cyan
    exit 0
}

Write-Host ''
Write-Host 'Resetting local WASH data...' -ForegroundColor Yellow
Write-Host ''

$deletedItems = New-Object System.Collections.Generic.List[string]
$warnings = New-Object System.Collections.Generic.List[string]

function Remove-RuntimeFile {
    param([Parameter(Mandatory)][string]$FullPath, [Parameter(Mandatory)][string]$RelativeLabel)

    if (-not (Test-Path -LiteralPath $FullPath -PathType Leaf)) {
        return
    }
    try {
        Remove-Item -LiteralPath $FullPath -Force -ErrorAction Stop
        $deletedItems.Add($RelativeLabel)
    } catch {
        $warnings.Add("Could not delete $RelativeLabel -- $($_.Exception.Message)")
    }
}

# Ensure data/ exists (nothing to reset otherwise, but the directory
# itself must always be preserved/present after this script runs).
if (-not (Test-Path -LiteralPath $dataDirectory -PathType Container)) {
    New-Item -ItemType Directory -Path $dataDirectory -Force | Out-Null
}

# 1. Known runtime files.
foreach ($fileName in $knownRuntimeFileNames) {
    $filePath = Join-Path $dataDirectory $fileName
    Remove-RuntimeFile -FullPath $filePath -RelativeLabel "data/$fileName"
}

# 2. Any other migration backup artifacts directly under data/ beyond the
#    known list above (e.g. a future *.pre_migration.bak from a different
#    repository). Non-recursive: data/backups/ is handled separately below.
if (Test-Path -LiteralPath $dataDirectory -PathType Container) {
    Get-ChildItem -LiteralPath $dataDirectory -Filter '*.bak' -File -ErrorAction SilentlyContinue |
        ForEach-Object {
            Remove-RuntimeFile -FullPath $_.FullName -RelativeLabel "data/$($_.Name)"
        }
}

# 3. Clear data/backups/ contents (recursively) while preserving the
#    directory itself and any source-controlled placeholder files.
if (Test-Path -LiteralPath $backupsDirectory -PathType Container) {
    Get-ChildItem -LiteralPath $backupsDirectory -Recurse -File -ErrorAction SilentlyContinue |
        Where-Object { $preservedBackupFileNames -notcontains $_.Name } |
        ForEach-Object {
            $relative = 'data/backups/' + $_.FullName.Substring($backupsDirectory.Length + 1).Replace('\', '/')
            Remove-RuntimeFile -FullPath $_.FullName -RelativeLabel $relative
        }

    # Remove now-empty backup kind subfolders (manual/, scheduled/, etc.),
    # deepest first, but never the data/backups/ directory itself.
    Get-ChildItem -LiteralPath $backupsDirectory -Recurse -Directory -ErrorAction SilentlyContinue |
        Sort-Object { $_.FullName.Length } -Descending |
        ForEach-Object {
            $remaining = Get-ChildItem -LiteralPath $_.FullName -Force -ErrorAction SilentlyContinue
            if (-not $remaining) {
                try {
                    Remove-Item -LiteralPath $_.FullName -Force -ErrorAction Stop
                } catch {
                    # An empty leftover subfolder is harmless -- not worth failing the run over.
                }
            }
        }
} else {
    New-Item -ItemType Directory -Path $backupsDirectory -Force | Out-Null
}

Write-Host ''
if ($deletedItems.Count -eq 0) {
    Write-Host 'No local runtime data was found to delete.' -ForegroundColor Cyan
} else {
    Write-Host "Deleted $($deletedItems.Count) item(s):" -ForegroundColor Green
    foreach ($item in $deletedItems) {
        Write-Host "  - $item"
    }
}

if ($warnings.Count -gt 0) {
    Write-Host ''
    Write-Host 'Some items could not be deleted:' -ForegroundColor Red
    foreach ($warning in $warnings) {
        Write-Host "  - $warning" -ForegroundColor Red
    }
}

Write-Host ''
Write-Host 'data/ and data/backups/ have been preserved.' -ForegroundColor Cyan
Write-Host 'The next WASH bot startup should behave like a fresh installation.' -ForegroundColor Green
Write-Host ''
