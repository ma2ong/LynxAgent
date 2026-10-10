# 注册多因子模型影子留痕任务：周一到周五 17:30 跑 scripts\ml_shadow.py（周末电脑关机）。
# 脚本自己判断该记哪一周、记过就跳过，所以每天跑只有周五（或周五漏了的下周一）真的训练。
# 计划任务默认低优先级（BelowNormal），训练几分钟、几 GB 内存，不会抢后端。
$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "..")
$python = "C:\Users\Administrator\AppData\Local\Programs\Python\Python314\python.exe"
$log = Join-Path $root "runtime\ml_shadow.log"

$action = New-ScheduledTaskAction `
    -Execute "cmd.exe" `
    -Argument "/c `"`"$python`" scripts\ml_shadow.py >> `"$log`" 2>&1`"" `
    -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At 17:30
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

Write-Host "LynxAgent-MLShadow registered (Mon-Fri 17:30). Log: $log"
