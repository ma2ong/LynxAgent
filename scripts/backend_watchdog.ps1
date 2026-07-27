# One-shot backend supervisor, driven by the LynxAgentBackend scheduled task.
# Keep this file ASCII-only because Windows PowerShell 5.1 reads BOM-less files
# using the system ANSI code page.

$ErrorActionPreference = "Continue"
$root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $root

$log = Join-Path $root "backend.watchdog.log"
$python = "C:\Users\Administrator\AppData\Local\Programs\Python\Python314\python.exe"
$failureStamp = Join-Path $root "runtime\backend.unhealthy.since"

if ((Test-Path $log) -and (Get-Item $log).Length -gt 10MB) {
    Move-Item -Force $log "$log.1"
}

function Test-BackendHealthy {
    try {
        $response = Invoke-RestMethod -Uri "http://127.0.0.1:8001/api/health" -TimeoutSec 5
        return $response.status -eq "healthy"
    } catch {
        return $false
    }
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
