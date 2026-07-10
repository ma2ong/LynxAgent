# LynxAgent backend watchdog: restart uvicorn whenever it exits.
# Hosted by scheduled task "LynxAgentBackend" (survives agent-session process reaping).
# Note: keep this file ASCII-only - PS 5.1 reads BOM-less files as ANSI/GBK.
$ErrorActionPreference = "Continue"
$root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $root

$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$env:USE_MONGODB_STORAGE = "false"
$env:NO_PROXY = "localhost,127.0.0.1,.eastmoney.com,eastmoney.com,.sina.com.cn,sina.com.cn,sinajs.cn,.sse.com.cn,sse.com.cn,.szse.cn,szse.cn,csindex.com.cn,gtimg.cn,qt.gtimg.cn,.163.com,163.com,baostock.com,tushare.pro,cninfo.com.cn,akfamily.xyz"
$env:no_proxy = $env:NO_PROXY

$log = Join-Path $root "backend.watchdog.log"
$python = "C:\Users\Administrator\AppData\Local\Programs\Python\Python314\python.exe"
"[$(Get-Date -Format o)] watchdog started (pid $PID)" | Out-File -FilePath $log -Append -Encoding utf8

# Another live instance already serves 8001 -> exit at once.
# The scheduled task re-runs every minute, so a killed tree is revived within 60s
# while healthy periods spawn nothing (this instance keeps running as long as uvicorn does).
$listening = Get-NetTCPConnection -LocalPort 8001 -State Listen -ErrorAction SilentlyContinue
if ($listening) {
    exit 0
}

while ($true) {
    "[$(Get-Date -Format o)] launching uvicorn..." | Out-File -FilePath $log -Append -Encoding utf8
    try {
        & $python -m uvicorn app.lite_main:app --host 127.0.0.1 --port 8001 `
            1>> (Join-Path $root "backend.out.log") 2>> (Join-Path $root "backend.err.log")
    } catch {
        "[$(Get-Date -Format o)] EXCEPTION: $($_.Exception.Message)" | Out-File -FilePath $log -Append -Encoding utf8
    }
    "[$(Get-Date -Format o)] uvicorn exited (code $LASTEXITCODE); restart in 2s" | Out-File -FilePath $log -Append -Encoding utf8
    Start-Sleep -Seconds 2
}
