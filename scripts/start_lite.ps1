param(
  [switch]$NoFrontend,
  [switch]$NoBackend,
  [switch]$NoOpen,
  [int]$BackendPort = 8001,
  [int]$FrontendPort = 5173
)

# LynxAgent SaaS Lite 本地预览一键启动
#   后端 app.lite_main (uvicorn) + 前端 vite，两个独立 detached 进程。
#   后端默认 8001（避开 8000，常被其他后端占用），前端 5173。
#   幂等：端口已在监听则跳过，不重复拉起。

$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $root

function Test-PortListening([int]$Port) {
  return [bool](Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)
}

function Wait-Port([int]$Port, [string]$Name) {
  for ($i = 0; $i -lt 45; $i++) {
    if (Test-PortListening $Port) { Write-Host "$Name ready on port $Port"; return }
    Start-Sleep -Seconds 1
  }
  throw "$Name did not become ready on port $Port — check log."
}

$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$env:USE_MONGODB_STORAGE = "false"
# 国内行情数据源直连，绕过系统代理(Clash 等)；海外 LLM 调用仍走代理
$env:NO_PROXY = "localhost,127.0.0.1,.eastmoney.com,eastmoney.com,.sina.com.cn,sina.com.cn,sinajs.cn,.sse.com.cn,sse.com.cn,.szse.cn,szse.cn,csindex.com.cn,gtimg.cn,qt.gtimg.cn,.163.com,163.com,baostock.com,tushare.pro,cninfo.com.cn,akfamily.xyz"
$env:no_proxy = $env:NO_PROXY

if (-not $NoBackend) {
  if (Test-PortListening $BackendPort) {
    Write-Host "Backend port $BackendPort already in use — skipping launch."
  } else {
    Start-Process -FilePath "python" `
      -ArgumentList "-m","uvicorn","app.lite_main:app","--host","127.0.0.1","--port","$BackendPort" `
      -WorkingDirectory $root -WindowStyle Hidden `
      -RedirectStandardOutput "$root\backend.out.log" `
      -RedirectStandardError  "$root\backend.err.log"
    Wait-Port $BackendPort "Backend (app.lite_main)"
  }
  Write-Host "Backend:  http://localhost:$BackendPort   (docs: /docs)"
}

if (-not $NoFrontend) {
  # 让 vite 代理跟随后端端口（vite.config 默认读 VITE_DEV_API_TARGET）
  $env:VITE_DEV_API_TARGET = "http://127.0.0.1:$BackendPort"
  if (Test-PortListening $FrontendPort) {
    Write-Host "Frontend port $FrontendPort already in use — skipping launch."
  } else {
    $feDir = Join-Path $root "frontend"
    $vite  = Join-Path $feDir "node_modules\vite\bin\vite.js"
    if (-not (Test-Path $vite)) { throw "vite not found: $vite — run 'npm install' in frontend/ first." }
    # 直接用 node 跑 vite，绕开 npm.cmd 批处理 shim（shim 的子进程会在会话结束后被回收）
    Start-Process -FilePath "node" -ArgumentList $vite `
      -WorkingDirectory $feDir -WindowStyle Hidden `
      -RedirectStandardOutput "$root\frontend.out.log" `
      -RedirectStandardError  "$root\frontend.err.log"
    Wait-Port $FrontendPort "Frontend (vite)"
  }
  Write-Host "Frontend: http://localhost:$FrontendPort"

  if (-not $NoOpen) { Start-Process "http://localhost:$FrontendPort" }
}

Write-Host ""
Write-Host "Mode: LynxAgent SaaS Lite (SQLite, no MongoDB/Docker). Logs: backend.*.log / frontend.*.log"
