param(
    [Parameter(Mandatory = $true)][string]$HostName,
    [string]$User = "ubuntu",
    [string]$KeyPath = "$env:USERPROFILE\.ssh\lynxagent_oci_ed25519",
    [switch]$SkipData
)

$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "..")
$work = Join-Path $root "runtime\oracle-deploy"
$staging = Join-Path $work "release"
$data = Join-Path $work "data"
$releaseArchive = Join-Path $work "astockpick-release.tar.gz"
$dataArchive = Join-Path $work "astockpick-data.tar.gz"
$remote = "${User}@${HostName}"

if (-not (Test-Path $KeyPath)) {
    throw "SSH key not found: $KeyPath"
}
if (-not (Test-Path (Join-Path $root ".env"))) {
    throw ".env not found"
}

$resolvedWork = [IO.Path]::GetFullPath($work)
$resolvedRuntime = [IO.Path]::GetFullPath((Join-Path $root "runtime"))
if (-not $resolvedWork.StartsWith($resolvedRuntime, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Unsafe deployment workspace: $resolvedWork"
}

if (Test-Path $work) {
    Remove-Item -LiteralPath $work -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $staging, $data | Out-Null

Write-Host "[1/6] Building frontend..."
Push-Location (Join-Path $root "frontend")
cmd /c "npm run build 2>&1"
$buildExit = $LASTEXITCODE
Pop-Location
if ($buildExit -ne 0) {
    throw "Frontend build failed with exit code $buildExit"
}

Write-Host "[2/6] Staging application..."
$robocopyArgs = @(
    $root,
    $staging,
    "/E",
    "/XD", ".git", ".venv", "node_modules", "runtime", "logs", "reports", ".playwright-mcp",
    "/XF", ".env", ".env.*", "*.log", "*.sqlite", "*.sqlite-shm", "*.sqlite-wal", "*.pyc"
)
& robocopy @robocopyArgs | Out-Null
if ($LASTEXITCODE -gt 7) {
    throw "Robocopy failed with exit code $LASTEXITCODE"
}

tar -czf $releaseArchive -C $staging .
if ($LASTEXITCODE -ne 0) {
    throw "Failed to create release archive"
}

if (-not $SkipData) {
    Write-Host "[3/6] Creating consistent SQLite export..."
    & "C:\Users\Administrator\AppData\Local\Programs\Python\Python314\python.exe" `
        (Join-Path $root "scripts\sqlite_export.py") `
        --runtime (Join-Path $root "runtime") `
        --output $data
    if ($LASTEXITCODE -ne 0) {
        throw "SQLite export failed"
    }
    tar -czf $dataArchive -C $data .
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create data archive"
    }
} else {
    Write-Host "[3/6] Keeping existing server data."
}

Write-Host "[4/6] Uploading release..."
$sshOptions = @("-i", $KeyPath, "-o", "StrictHostKeyChecking=accept-new")
function Copy-RemoteFile {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Destination
    )

    & scp @sshOptions $Source $Destination
    if ($LASTEXITCODE -ne 0) {
        throw "Upload failed: $Source"
    }
}

Copy-RemoteFile $releaseArchive "${remote}:/tmp/astockpick-release.tar.gz"
Copy-RemoteFile (Join-Path $root ".env") "${remote}:/tmp/astockpick.env"
Copy-RemoteFile (Join-Path $root "deploy\oracle\install.sh") "${remote}:/tmp/astockpick-install.sh"
if (-not $SkipData) {
    Copy-RemoteFile $dataArchive "${remote}:/tmp/astockpick-data.tar.gz"
}

Write-Host "[5/6] Installing on Oracle Cloud..."
$remoteData = if ($SkipData) { "/tmp/no-data-archive" } else { "/tmp/astockpick-data.tar.gz" }
& ssh @sshOptions $remote `
    "chmod 600 /tmp/astockpick.env && sudo bash /tmp/astockpick-install.sh /tmp/astockpick-release.tar.gz /tmp/astockpick.env $remoteData"
if ($LASTEXITCODE -ne 0) {
    throw "Remote installation failed"
}

Write-Host "[6/6] Verifying public health..."
$health = Invoke-RestMethod -Uri "http://$HostName/api/health" -TimeoutSec 20
if ($health.status -ne "healthy") {
    throw "Public health check failed"
}

Write-Host ""
Write-Host "Deployment complete: http://$HostName"
