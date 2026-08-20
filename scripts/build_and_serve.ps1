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
    # Hash each backend path's tree/blob, not the global HEAD: otherwise a
    # frontend-only commit bumps HEAD and forces a needless backend restart.
    # --verify -q fails cleanly (no output / no stderr) on paths that don't exist.
    # Route git through cmd /c + 2>nul: PS 5.1 wraps a native command's stderr in an
    # ErrorRecord, which aborts the script under -EA Stop. Whenever app/ or quantcore/
    # has uncommitted changes, git diff prints an autocrlf "LF will be replaced by CRLF"
    # warning and the deploy dies. Same trap as taskkill below.
    $head = (
        $backendPaths | ForEach-Object {
            $p = $_
            $h = cmd /c "git rev-parse --verify -q `"HEAD:$p`" 2>nul"
            if ($h) { "$h".Trim() }
        } | Where-Object { $_ }
    ) -join ";"
    $pathArgs = ($backendPaths | ForEach-Object { "`"$_`"" }) -join " "
    $diff = (cmd /c "git diff --no-ext-diff --binary HEAD -- $pathArgs 2>nul" | Out-String)
    $untracked = (cmd /c "git ls-files --others --exclude-standard -- $pathArgs 2>nul" | Out-String)
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

# vite deliberately does not empty dist (see vite.config.ts): old hashed assets stay so a
# page opened moments before a deploy can still lazy-load its chunks. The cost is unbounded
# growth - by 2026-08-06 that was 1637 files / 23 MB including 70 copies of AppLayout,
# enough noise to make a healthy deploy look broken while debugging. Prune by age, never
# touching anything the current build still reaches.
# Keep this file ASCII: it has no BOM, and PS 5.1 decodes BOM-less files as system ANSI.
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "prune_dist.ps1")

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
# Hold a lock across the restart so the watchdog stands down. Both scripts
# restart the backend; on 2026-08-19 they raced and left three uvicorn
# processes fighting over port 8001, two of which could neither bind nor write
# their own error log, so they died silently and the watchdog kept spawning
# more. try/finally because a failed deploy must still release the lock.
$deployLock = Join-Path $root "runtime\backend.deploy.lock"
New-Item -ItemType Directory -Force -Path (Split-Path $deployLock) | Out-Null
[System.IO.File]::WriteAllText(
    $deployLock,
    (Get-Date).ToUniversalTime().ToString("o"),
    (New-Object System.Text.UTF8Encoding($false))
)

try {
    Write-Host "[2/3] Restarting backend ($reason)..."
    $connections = Get-NetTCPConnection -LocalPort 8001 -State Listen -ErrorAction SilentlyContinue
    if ($connections) {
        # Kill the whole process tree (/T): the backend's ProcessPoolExecutor scan
        # worker is a child process; killing only the backend PID orphans it, and
        # orphans accumulate across restarts and eat memory. cmd /c + >nul swallows
        # taskkill output so its stderr can't abort the script under -EA Stop.
        $connections | Select-Object -ExpandProperty OwningProcess -Unique |
            ForEach-Object { cmd /c "taskkill /PID $_ /T /F >nul 2>&1" }
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
} finally {
    Remove-Item $deployLock -Force -ErrorAction SilentlyContinue
}

[System.IO.File]::WriteAllText(
    $signatureFile,
    $currentSignature,
    (New-Object System.Text.UTF8Encoding($false))
)

Write-Host ""
Write-Host "Deployment complete; backend is healthy."
