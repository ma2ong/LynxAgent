# One-shot: start the ngrok tunnel if it is not already running.
# Driven by the scheduled task "LynxAgentNgrok" (boot + every minute).
# Same design as the backend supervisor: never resident, so it cannot hang and
# block later triggers.
#
# ASCII-only: PS 5.1 decodes BOM-less UTF-8 as GBK.

$ErrorActionPreference = "Continue"
$root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $root

$exe = Join-Path $root "runtime\ngrok\ngrok.exe"
$log = Join-Path $root "runtime\ngrok\ngrok.log"

# already up -> nothing to do
if (Get-Process ngrok -ErrorAction SilentlyContinue) { exit 0 }

# don't publish a dead backend (visitors would get 502)
$backend = Get-NetTCPConnection -LocalPort 8001 -State Listen -ErrorAction SilentlyContinue
if (-not $backend) { exit 0 }

"[$(Get-Date -Format o)] starting ngrok tunnel" | Out-File -FilePath $log -Append -Encoding utf8

Start-Process -FilePath $exe `
    -ArgumentList "start", "lynxagent", "--log", "stdout" `
    -RedirectStandardOutput (Join-Path $root "runtime\ngrok\ngrok.out.log") `
    -RedirectStandardError (Join-Path $root "runtime\ngrok\ngrok.err.log") `
    -WindowStyle Hidden

Start-Sleep -Seconds 5
if (Get-Process ngrok -ErrorAction SilentlyContinue) {
    Write-Output "ngrok tunnel started"
} else {
    Write-Output "ngrok failed to start; see runtime\ngrok\ngrok.err.log"
}
