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
$startTime = (Get-Date).AddMinutes(1)
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`""
$trigger = New-ScheduledTaskTrigger -Once -At $startTime -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes) -RepetitionDuration ([TimeSpan]::MaxValue)
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null

Write-Host "Scheduled Task '$taskName' installed with interval $IntervalMinutes minutes."
