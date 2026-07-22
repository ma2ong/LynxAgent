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

# The scheduled task runs as SYSTEM, whose profile has no ngrok.yml, so ngrok
# resolved an empty config and failed with "Tunnel 'lynxagent' is not defined"
# once a minute forever. Point it at the real config explicitly instead of
# relying on whatever account happens to run this. Keep the authtoken out of
# the repo - the file stays in the user profile.
$config = "C:\Users\Administrator\AppData\Local\ngrok\ngrok.yml"

# "process alive" is not the same as "tunnel up": a heartbeat timeout leaves the
# agent running in a reconnect loop while visitors get ERR_NGROK_3200. Ask the
# local inspector whether the tunnel is actually published.
#
# ngrok reconnects on its own within a minute or so, and killing it mid-recovery
# only makes the outage longer - so a single bad probe just leaves a stamp, and
# only a tunnel that has been down across $graceMinutes gets force-restarted.
# That still covers the case ngrok cannot fix itself (wedged reconnect loop).
$stamp = Join-Path $root "runtime\ngrok\unhealthy.stamp"
$graceMinutes = 3

$proc = Get-Process ngrok -ErrorAction SilentlyContinue
if ($proc) {
    $healthy = $false
    try {
        $api = Invoke-RestMethod -Uri "http://127.0.0.1:4040/api/tunnels" -TimeoutSec 5
        $healthy = @($api.tunnels).Count -gt 0
    } catch { $healthy = $false }

    if ($healthy) {
        Remove-Item $stamp -ErrorAction SilentlyContinue
        exit 0
    }

    if (-not (Test-Path $stamp)) {
        (Get-Date -Format o) | Out-File -FilePath $stamp -Encoding utf8
        "[$(Get-Date -Format o)] tunnel down, letting ngrok reconnect" | Out-File -FilePath $log -Append -Encoding utf8
        exit 0
    }

    $since = Get-Date (Get-Content $stamp -First 1)
    if (((Get-Date) - $since).TotalMinutes -lt $graceMinutes) { exit 0 }

    "[$(Get-Date -Format o)] tunnel still down after $graceMinutes min; restarting ngrok" | Out-File -FilePath $log -Append -Encoding utf8
    Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
}
Remove-Item $stamp -ErrorAction SilentlyContinue

# don't publish a dead backend (visitors would get 502)
$backend = Get-NetTCPConnection -LocalPort 8001 -State Listen -ErrorAction SilentlyContinue
if (-not $backend) { exit 0 }

# no config -> retrying every minute would just spam the log; say so and stop
if (-not (Test-Path $config)) {
    "[$(Get-Date -Format o)] ngrok config missing: $config" | Out-File -FilePath $log -Append -Encoding utf8
    exit 1
}

"[$(Get-Date -Format o)] starting ngrok tunnel" | Out-File -FilePath $log -Append -Encoding utf8

Start-Process -FilePath $exe `
    -ArgumentList "start", "lynxagent", "--config", "`"$config`"", "--log", "stdout" `
    -RedirectStandardOutput (Join-Path $root "runtime\ngrok\ngrok.out.log") `
    -RedirectStandardError (Join-Path $root "runtime\ngrok\ngrok.err.log") `
    -WindowStyle Hidden

Start-Sleep -Seconds 5
if (Get-Process ngrok -ErrorAction SilentlyContinue) {
    Write-Output "ngrok tunnel started"
} else {
    Write-Output "ngrok failed to start; see runtime\ngrok\ngrok.err.log"
}
