# LynxAgent backend supervisor: one-shot check-and-start, driven by the scheduled
# task "LynxAgentBackend" (repeats every minute, survives agent-session reaping).
#
# Why one-shot instead of a resident restart loop: the old loop kept relaunching
# uvicorn every 2s whenever port 8001 was held by someone else (crashloop spam in
# the log), and a stuck instance blocked the task's single-instance policy, so a
# dead backend was never revived (observed 2026-07-14: 08:10 -> 12:05 with no backend).
# One-shot cannot hang, cannot hot-spin, and the 1-minute trigger is the supervision.
#
# Note: keep this file ASCII-only - PS 5.1 reads BOM-less files as ANSI/GBK.
$ErrorActionPreference = "Continue"
$root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $root

$log = Join-Path $root "backend.watchdog.log"
$python = "C:\Users\Administrator\AppData\Local\Programs\Python\Python314\python.exe"

# Healthy: something already serves 8001 -> nothing to do.
$listening = Get-NetTCPConnection -LocalPort 8001 -State Listen -ErrorAction SilentlyContinue
if ($listening) {
    exit 0
}

"[$(Get-Date -Format o)] port 8001 down; starting uvicorn" | Out-File -FilePath $log -Append -Encoding utf8

$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$env:USE_MONGODB_STORAGE = "false"
$env:NO_PROXY = "localhost,127.0.0.1,.eastmoney.com,eastmoney.com,.sina.com.cn,sina.com.cn,sinajs.cn,.sse.com.cn,sse.com.cn,.szse.cn,szse.cn,csindex.com.cn,gtimg.cn,qt.gtimg.cn,.163.com,163.com,baostock.com,tushare.pro,cninfo.com.cn,akfamily.xyz"
$env:no_proxy = $env:NO_PROXY

# Detached child: this script exits immediately, uvicorn keeps running.
Start-Process -FilePath $python `
    -ArgumentList "-m", "uvicorn", "app.lite_main:app", "--host", "127.0.0.1", "--port", "8001" `
    -WorkingDirectory $root `
    -RedirectStandardOutput (Join-Path $root "backend.out.log") `
    -RedirectStandardError (Join-Path $root "backend.err.log") `
    -WindowStyle Hidden

exit 0
