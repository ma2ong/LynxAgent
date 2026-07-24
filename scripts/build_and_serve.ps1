# Production frontend build with an on-demand backend restart.
#
# The backend serves frontend/dist. Frontend-only releases must not restart the
# API because doing so creates avoidable public downtime.

param(
    [switch]$ForceRestart
)

$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $root

function Test-BackendHealthy {
    try {
        $response = Invoke-RestMethod -Uri "http://127.0.0.1:8001/api/health" -TimeoutSec 5
        return $response.status -eq "healthy"
    } catch {
        return $false
    }
}

function Get-BackendSignature {
    $backendPaths = @(
        "app",
        "quantcore",
        "scripts/backend_watchdog.ps1",
        "requirements.txt",
        "requirements-prod.txt",
        "pyproject.toml"
    )
    $head = (& git rev-parse HEAD 2>$null | Out-String).Trim()
    $diff = (& git diff --no-ext-diff --binary HEAD -- $backendPaths 2>$null | Out-String)
    $untracked = (& git ls-files --others --exclude-standard -- $backendPaths 2>$null | Out-String)
    $environment = @(".env", ".env.local") |
        Where-Object { Test-Path $_ } |
        ForEach-Object { "$_`n$(Get-Content -Raw $_)" } |
        Out-String
    $payload = "$head`n$diff`n$untracked`n$environment"
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($payload)
    $sha256 = [System.Security.Cryptography.SHA256]::Create()
    try {
        return ([BitConverter]::ToString($sha256.ComputeHash($bytes))).Replace("-", "").ToLowerInvariant()
    } finally {
        $sha256.Dispose()
    }
}

Write-Host "[1/3] Building frontend..."
Push-Location (Join-Path $root "frontend")
cmd /c "npm run build 2>&1"
$buildExit = $LASTEXITCODE
Pop-Location
if ($buildExit -ne 0) {
    throw "Frontend build failed (npm run build exit code $buildExit)"
}

$signatureFile = Join-Path $root "runtime\backend.deploy.signature"
$currentSignature = Get-BackendSignature
$deployedSignature = if (Test-Path $signatureFile) {
    (Get-Content -Raw $signatureFile).Trim()
} else {
    ""
}
$backendHealthy = Test-BackendHealthy
$needsRestart = $ForceRestart -or -not $backendHealthy -or $currentSignature -ne $deployedSignature

if (-not $needsRestart) {
    Write-Host "[2/3] Backend unchanged; keeping the current process (zero downtime)."
    Write-Host "[3/3] Health check passed."
    Write-Host ""
    Write-Host "Deployment complete."
    exit 0
}

$reason = if ($ForceRestart) {
    "forced"
} elseif (-not $backendHealthy) {
    "health check failed"
} elseif (-not $deployedSignature) {
    "initial deployment signature"
} else {
    "backend changed"
}
Write-Host "[2/3] Restarting backend ($reason)..."
$connections = Get-NetTCPConnection -LocalPort 8001 -State Listen -ErrorAction SilentlyContinue
if ($connections) {
    $connections | Select-Object -ExpandProperty OwningProcess -Unique |
        ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }
}

for ($attempt = 0; $attempt -lt 20; $attempt++) {
    if (-not (Get-NetTCPConnection -LocalPort 8001 -State Listen -ErrorAction SilentlyContinue)) {
        break
    }
    Start-Sleep -Milliseconds 250
}

try {
    Start-ScheduledTask -TaskName "LynxAgentBackend"
} catch {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $root "scripts\backend_watchdog.ps1")
    if ($LASTEXITCODE -ne 0) {
        throw "Backend watchdog failed (exit code $LASTEXITCODE)"
    }
}

Write-Host "[3/3] Waiting for backend health..."
$ready = $false
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    Start-Sleep -Seconds 2
    if (Test-BackendHealthy) {
        $ready = $true
        break
    }
}
if (-not $ready) {
    throw "Backend did not recover within 60 seconds; inspect backend.err.log"
}

[System.IO.File]::WriteAllText(
    $signatureFile,
    $currentSignature,
    (New-Object System.Text.UTF8Encoding($false))
)

Write-Host ""
Write-Host "Deployment complete; backend is healthy."
