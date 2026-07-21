# 生产构建 + 重启后端：改完代码跑这个，线上才会是新版本。
#
# 后端直接托管 frontend/dist（单一来源，隧道只需暴露 8001），
# 所以前端不重新构建的话，线上永远停在旧页面——这是最容易踩的上线坑。

$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $root

Write-Host "[1/3] 构建前端…"
Push-Location (Join-Path $root "frontend")
# vite/rollup 的告警走 stderr（如 @vueuse 的 /*#__PURE__*/ 注释），PS 5.1 会把每行 stderr
# 包成 ErrorRecord，叠加 ErrorActionPreference=Stop 会让构建"成功却中止"。
# 用 cmd /c 在 cmd 层合并 stderr（PS 只拿到纯文本），再用退出码判定真成败。
cmd /c "npm run build 2>&1"
$buildExit = $LASTEXITCODE
Pop-Location
if ($buildExit -ne 0) { throw "前端构建失败（npm run build 退出码 $buildExit）" }

Write-Host "[2/3] 重启后端（看门狗会在 60 秒内自动拉起）…"
$conn = Get-NetTCPConnection -LocalPort 8001 -State Listen -ErrorAction SilentlyContinue
if ($conn) { Stop-Process -Id $conn.OwningProcess -Force }

Write-Host "[3/3] 等待健康检查…"
for ($i = 0; $i -lt 20; $i++) {
    Start-Sleep -Seconds 5
    try {
        $r = Invoke-RestMethod -Uri "http://127.0.0.1:8001/api/health" -TimeoutSec 5
        if ($r.status -eq "healthy") { Write-Host "后端已就绪：$($r.service)"; break }
    } catch { }
}

Write-Host ""
Write-Host "完成。线上地址见 Cloudflare 隧道（scripts\deploy_cloudflare.ps1）。"
