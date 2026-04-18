param(
    [int]$IntervalMinutes = 5
)

$ErrorActionPreference = 'Stop'

if ($IntervalMinutes -lt 5 -or $IntervalMinutes -gt 10) {
    throw 'IntervalMinutes must be between 5 and 10.'
}

$repoRoot = Split-Path -Parent $PSScriptRoot
$taskName = 'Arbivision Auto Update'
$scriptPath = Join-Path $repoRoot 'utilities\run_auto_update.ps1'
$startTime = (Get-Date).AddMinutes(1).ToString('HH:mm')
$taskCommand = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File ""$scriptPath"""
$taskNameArg = '"' + $taskName + '"'

& schtasks.exe /Create /F /TN $taskNameArg /SC MINUTE /MO $IntervalMinutes /ST $startTime /TR $taskCommand

if ($LASTEXITCODE -ne 0) {
    throw "failed to create scheduled task, exit code: $LASTEXITCODE"
}

Write-Host "Scheduled Task '$taskName' installed with interval $IntervalMinutes minutes."
