# Register the one-shot backend watchdog as a reliable Windows scheduled task.

$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "..")
$runner = Join-Path $root "scripts\backend_watchdog.ps1"

if (-not (Test-Path $runner)) {
    throw "Watchdog script not found: $runner"
}

$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$runner`""
$startup = New-ScheduledTaskTrigger -AtStartup
$repeating = New-ScheduledTaskTrigger `
    -Once `
    -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes 1)
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 2)

Register-ScheduledTask `
    -TaskName "LynxAgentBackend" `
    -Action $action `
    -Trigger $startup, $repeating `
    -Settings $settings `
    -RunLevel Highest `
    -User "SYSTEM" `
    -Force | Out-Null

Start-ScheduledTask -TaskName "LynxAgentBackend"
Start-Sleep -Seconds 3

$task = Get-ScheduledTaskInfo -TaskName "LynxAgentBackend"
Write-Host "LynxAgentBackend registered. LastTaskResult=$($task.LastTaskResult)"
