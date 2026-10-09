# 注册多因子模型影子留痕的周任务（2026-10-09）：每周六 03:00 跑 scripts\ml_shadow.py。
# 计划任务默认低优先级（BelowNormal），训练几分钟、几 GB 内存，不会抢后端。
$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "..")
$python = "C:\Users\Administrator\AppData\Local\Programs\Python\Python314\python.exe"
$log = Join-Path $root "runtime\ml_shadow.log"

$action = New-ScheduledTaskAction `
    -Execute "cmd.exe" `
    -Argument "/c `"`"$python`" scripts\ml_shadow.py >> `"$log`" 2>&1`"" `
    -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Saturday -At 3:00
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2)

Register-ScheduledTask `
    -TaskName "LynxAgent-MLShadow" `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -User "SYSTEM" `
    -Force | Out-Null

Write-Host "LynxAgent-MLShadow registered (Saturday 03:00). Log: $log"
