# LynxAgent deploy: ngrok reserved domain (serves BOTH frontend and API).
#
# The backend already hosts the built SPA (single origin on 127.0.0.1:8001), so one
# ngrok domain publishes the whole app - no Vercel, no CORS, no second deployment.
#
# Usage:
#   .\scripts\deploy_ngrok.ps1 -Authtoken 2xxxxx -Domain your-name.ngrok-free.app
#
# What it does:
#   1) stores the authtoken
#   2) writes ngrok config (reserved domain -> local 8001)
#   3) registers a scheduled task (boot + 1min self-heal, same one-shot design as
#      the backend supervisor: a resident loop can hang and never revive)
#   4) stops the temporary trycloudflare tunnel
#   5) records the public URL in runtime\PUBLIC_URL.txt
#
# ASCII-only on purpose: PS 5.1 decodes BOM-less UTF-8 as GBK and mangles the file.

param(
    [Parameter(Mandatory = $true)][string]$Authtoken,
    [Parameter(Mandatory = $true)][string]$Domain,
    [int]$Port = 8001
)

$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $root

$exe = Join-Path $root "runtime\ngrok\ngrok.exe"
if (-not (Test-Path $exe)) { throw "ngrok not installed: $exe" }

Write-Host "[1/5] storing authtoken..."
& $exe config add-authtoken $Authtoken | Out-Null

Write-Host "[2/5] writing config: $Domain -> 127.0.0.1:$Port ..."
$cfgDir = Join-Path $env:USERPROFILE "AppData\Local\ngrok"
New-Item -ItemType Directory -Force -Path $cfgDir | Out-Null
$cfg = @"
version: "2"
authtoken: $Authtoken
tunnels:
  lynxagent:
    proto: http
    addr: $Port
    domain: $Domain
"@
$cfgPath = Join-Path $cfgDir "ngrok.yml"
[System.IO.File]::WriteAllText($cfgPath, $cfg, (New-Object System.Text.UTF8Encoding($false)))

Write-Host "[3/5] stopping the temporary cloudflare tunnel..."
schtasks /Delete /TN LynxAgentTunnel /F 2>$null | Out-Null
Get-Process cloudflared -ErrorAction SilentlyContinue | Stop-Process -Force

Write-Host "[4/5] registering autostart task..."
$runner = Join-Path $root "scripts\ngrok_run.ps1"
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$runner`""
$t1 = New-ScheduledTaskTrigger -AtStartup
$t2 = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 1)
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName "LynxAgentNgrok" -Action $action -Trigger $t1, $t2 -Settings $settings -RunLevel Highest -User "SYSTEM" -Force | Out-Null

Write-Host "[5/5] starting the tunnel..."
& $runner
[System.IO.File]::WriteAllText((Join-Path $root "runtime\PUBLIC_URL.txt"), "https://$Domain",
    (New-Object System.Text.UTF8Encoding($false)))

Write-Host ""
Write-Host "=== live: https://$Domain ==="
Write-Host "Fixed address, survives restarts. Free plan shows an ngrok interstitial on the"
Write-Host "first browser visit - click 'Visit Site' once per browser."
