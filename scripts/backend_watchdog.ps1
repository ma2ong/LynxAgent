# One-shot backend supervisor, driven by the LynxAgentBackend scheduled task.
# Keep this file ASCII-only because Windows PowerShell 5.1 reads BOM-less files
# using the system ANSI code page.

$ErrorActionPreference = "Continue"
$root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $root

$log = Join-Path $root "backend.watchdog.log"
$python = "C:\Users\Administrator\AppData\Local\Programs\Python\Python314\python.exe"
$failureStamp = Join-Path $root "runtime\backend.unhealthy.since"
$deployLock = Join-Path $root "runtime\backend.deploy.lock"

if ((Test-Path $log) -and (Get-Item $log).Length -gt 10MB) {
    Move-Item -Force $log "$log.1"
}

# Stand down while a deploy owns the restart. Both this script and
# build_and_serve.ps1 restart the backend; on 2026-08-19 they raced and left
# three uvicorn processes fighting over port 8001. The two that lost could not
# bind, and could not write backend.err.log either because the winner held the
# redirect target open, so they died without leaving a trace and the watchdog
# spawned another one every minute.
#
# A stale lock is ignored on purpose: if a deploy dies mid-flight the watchdog
# must take over rather than stay disabled forever.
function Test-DeployInProgress {
    if (-not (Test-Path $deployLock)) { return $false }
    try {
        $since = [DateTime]::Parse((Get-Content -Raw $deployLock)).ToUniversalTime()
    } catch {
        Remove-Item $deployLock -Force -ErrorAction SilentlyContinue
        return $false
    }
    if (((Get-Date).ToUniversalTime() - $since).TotalMinutes -gt 10) {
        "[$(Get-Date -Format o)] deploy lock older than 10 min; ignoring it" |
            Out-File -FilePath $log -Append -Encoding utf8
        Remove-Item $deployLock -Force -ErrorAction SilentlyContinue
        return $false
    }
    return $true
}

# Never leave more than one backend behind. Anything already running that is
# not the healthy listener gets killed before a new one is started, otherwise
# failed starts pile up across restarts.
# Kill leftovers, but never a backend that is still coming up. The first cut of
# this killed anything named uvicorn before starting a new one, which included
# the instance started 60 seconds earlier that had not finished importing yet:
# it was killed, replaced, killed again on the next tick, and the service never
# got the chance to finish booting. Only processes older than the grace window
# below are treated as strays.
function Remove-StrayBackends {
    $cutoff = (Get-Date).AddMinutes(-3)
    $strays = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and $_.CommandLine -like "*uvicorn*app.lite_main*" -and $_.CreationDate -lt $cutoff }
    foreach ($p in $strays) {
        cmd /c "taskkill /PID $($p.ProcessId) /T /F >nul 2>&1"
        "[$(Get-Date -Format o)] killed stray backend PID $($p.ProcessId) (started $($p.CreationDate))" |
            Out-File -FilePath $log -Append -Encoding utf8
    }
    if ($strays) { Start-Sleep -Seconds 2 }
}

# Two attempts before calling it unhealthy. The loop still stalls on heavy
# aggregation (measured worst case ~11s on 2026-08-20), and a single timed-out
# probe during one of those stalls is not an outage. A backend that is really
# down fails both attempts instantly, so this costs nothing in the real case.
function Test-BackendHealthy {
    foreach ($attempt in 1..2) {
        try {
            $response = Invoke-RestMethod -Uri "http://127.0.0.1:8001/api/health" -TimeoutSec 15
            if ($response.status -eq "healthy") { return $true }
        } catch {}
        if ($attempt -eq 1) { Start-Sleep -Seconds 5 }
    }
    return $false
}

if (Test-DeployInProgress) {
    exit 0
}

$listening = Get-NetTCPConnection -LocalPort 8001 -State Listen -ErrorAction SilentlyContinue

if (Test-BackendHealthy) {
    Remove-Item $failureStamp -Force -ErrorAction SilentlyContinue
    exit 0
}

if ($listening) {
    if (-not (Test-Path $failureStamp)) {
        [System.IO.File]::WriteAllText(
            $failureStamp,
            (Get-Date).ToUniversalTime().ToString("o"),
            (New-Object System.Text.UTF8Encoding($false))
        )
        "[$(Get-Date -Format o)] port open but health failed; grace period started" |
            Out-File -FilePath $log -Append -Encoding utf8
        exit 0
    }

    try {
        $failedSince = [DateTime]::Parse((Get-Content -Raw $failureStamp)).ToUniversalTime()
    } catch {
        $failedSince = (Get-Date).ToUniversalTime().AddMinutes(-3)
    }

    if (((Get-Date).ToUniversalTime() - $failedSince).TotalSeconds -lt 120) {
        exit 0
    }

    "[$(Get-Date -Format o)] health failed for 120s; restarting backend" |
        Out-File -FilePath $log -Append -Encoding utf8
    # Kill the whole process tree (/T): the backend's ProcessPoolExecutor scan
    # worker is a child process; killing only the backend PID orphans it and
    # orphans accumulate across restarts. cmd /c + >nul swallows taskkill output
    # so its stderr isn't treated as an error.
    $listening | Select-Object -ExpandProperty OwningProcess -Unique |
        ForEach-Object { cmd /c "taskkill /PID $_ /T /F >nul 2>&1" }
    Start-Sleep -Seconds 2
}

Remove-Item $failureStamp -Force -ErrorAction SilentlyContinue

$maintenance = Join-Path $root "scripts\sqlite_maintenance.py"
if (Test-Path $maintenance) {
    & $python $maintenance 2>&1 |
        Out-File -FilePath $log -Append -Encoding utf8
}

Remove-StrayBackends

"[$(Get-Date -Format o)] backend unavailable; starting uvicorn" |
    Out-File -FilePath $log -Append -Encoding utf8

$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$env:USE_MONGODB_STORAGE = "false"
$env:NO_PROXY = "localhost,127.0.0.1,.eastmoney.com,eastmoney.com,.sina.com.cn,sina.com.cn,sinajs.cn,.sse.com.cn,sse.com.cn,.szse.cn,szse.cn,csindex.com.cn,gtimg.cn,qt.gtimg.cn,.163.com,163.com,baostock.com,tushare.pro,cninfo.com.cn,akfamily.xyz"
$env:no_proxy = $env:NO_PROXY

Start-Process -FilePath $python `
    -ArgumentList "-m", "uvicorn", "app.lite_main:app", "--host", "127.0.0.1", "--port", "8001" `
    -WorkingDirectory $root `
    -RedirectStandardOutput (Join-Path $root "backend.out.log") `
    -RedirectStandardError (Join-Path $root "backend.err.log") `
    -WindowStyle Hidden

exit 0
