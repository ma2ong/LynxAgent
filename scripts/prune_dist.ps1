# Prune stale hashed assets from frontend/dist/assets.
#
# Why this exists rather than emptyOutDir:true — vite.config.ts keeps old hashed
# assets on purpose: a browser that loaded index.html before a deploy still holds
# the old asset names, and lazily imports them when the user opens another page.
# Wiping the directory breaks that person mid-session. The cost of keeping them is
# unbounded growth: 2026-08-06 the directory held 1637 files / 23 MB, with 70
# copies of AppLayout alone, which is enough noise to make a real deploy look
# like a failed one while debugging.
#
# So: keep anything recent, keep anything the current build still points at, drop
# the rest. Two independent reasons to keep — a file only goes if neither holds.
#
#   powershell -File scripts/prune_dist.ps1              # keep 7 days, delete
#   powershell -File scripts/prune_dist.ps1 -KeepDays 14
#   powershell -File scripts/prune_dist.ps1 -DryRun      # report only

param(
    [int]$KeepDays = 7,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "..")
$dist = Join-Path $root "frontend\dist"
$assets = Join-Path $dist "assets"
$indexPath = Join-Path $dist "index.html"

if (-not (Test-Path $assets)) {
    Write-Host "No dist/assets yet; nothing to prune."
    exit 0
}
if (-not (Test-Path $indexPath)) {
    # 没有 index.html 就无法判断「当前构建还需要哪些」，宁可不删。
    Write-Host "dist/index.html missing — refusing to prune without it."
    exit 1
}

# 1) 当前 index.html 直接引用的资源，再沿 JS 里的 import 求传递闭包。
#    只信任「从当前 index.html 出发能到达的」，不靠文件名猜。
$assetPattern = '[A-Za-z0-9_\.\-]+-[A-Za-z0-9_\-]{6,}\.(?:js|css)'
$live = New-Object 'System.Collections.Generic.HashSet[string]'
$queue = New-Object System.Collections.Queue

$indexText = [System.IO.File]::ReadAllText($indexPath)
foreach ($m in [regex]::Matches($indexText, $assetPattern)) {
    if ($live.Add($m.Value)) { $queue.Enqueue($m.Value) }
}

while ($queue.Count -gt 0) {
    $name = $queue.Dequeue()
    $file = Join-Path $assets $name
    if (-not (Test-Path $file)) { continue }
    if ($name -notlike "*.js") { continue }
    $text = [System.IO.File]::ReadAllText($file)
    foreach ($m in [regex]::Matches($text, $assetPattern)) {
        if ($live.Add($m.Value)) { $queue.Enqueue($m.Value) }
    }
}

# 2) 近 KeepDays 天内产出的一律留着 —— 这条才是「部署前已打开的页面还能懒加载」的保障，
#    上面那条只保证当前构建自身完整。
$cutoff = (Get-Date).AddDays(-$KeepDays)

$all = Get-ChildItem -File $assets
$doomed = @($all | Where-Object {
    ($_.LastWriteTime -lt $cutoff) -and (-not $live.Contains($_.Name))
})

$keptRecent = @($all | Where-Object { $_.LastWriteTime -ge $cutoff }).Count
$freedMb = 0
if ($doomed.Count -gt 0) {
    $freedMb = [math]::Round((($doomed | Measure-Object Length -Sum).Sum) / 1MB, 1)
}

Write-Host "dist/assets: $($all.Count) files"
Write-Host "  in use by current build : $($live.Count)"
Write-Host "  newer than $KeepDays days      : $keptRecent"
Write-Host "  prunable                : $($doomed.Count)  (~$freedMb MB)"

if ($doomed.Count -eq 0) {
    Write-Host "Nothing to prune."
    exit 0
}
if ($DryRun) {
    Write-Host "DryRun: nothing deleted. Oldest few:"
    $doomed | Sort-Object LastWriteTime | Select-Object -First 5 |
        ForEach-Object { Write-Host "    $($_.LastWriteTime.ToString('yyyy-MM-dd'))  $($_.Name)" }
    exit 0
}

$doomed | Remove-Item -Force
Write-Host "Pruned $($doomed.Count) files, freed ~$freedMb MB."
