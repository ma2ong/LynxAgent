# LynxAgent 上线：Cloudflare Tunnel（命名隧道 + 开机自启服务）
#
# 架构：公网 --HTTPS--> Cloudflare 边缘 --加密隧道--> 本机 127.0.0.1:8001
#   · 不开放任何入站端口、不需要公网 IP、不需要备案国内服务器
#   · 后端仍跑在本机：744MB 行情库、定时任务、国内行情源都在本地，最快
#   · 邀请制由两道门保证：Cloudflare Access（邮箱白名单） + 应用自身登录（注册已关闭）
#
# 用法：
#   1) .\scripts\deploy_cloudflare.ps1 -Login              # 浏览器授权（选一个你的域名）
#   2) .\scripts\deploy_cloudflare.ps1 -Setup -Hostname lynx.你的域名.com
#   3) 到 Cloudflare Zero Trust 后台加 Access 策略（脚本最后会打印步骤）
#
# 注意：keep this file ASCII-comments-free? No - PS 5.1 reads BOM-less as GBK, 本文件存为 UTF-8 with BOM。

param(
    [switch]$Login,
    [switch]$Setup,
    [string]$Hostname = "",
    [string]$TunnelName = "lynxagent",
    [int]$Port = 8001
)

$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "..")
$exe = Join-Path $root "runtime\cloudflared\cloudflared.exe"
if (-not (Test-Path $exe)) { throw "cloudflared 未安装：$exe" }

if ($Login) {
    Write-Host "浏览器将打开 Cloudflare 授权页：选择要用的域名（zone），授权后会生成 cert.pem。"
    & $exe tunnel login
    Write-Host "授权完成。下一步：.\scripts\deploy_cloudflare.ps1 -Setup -Hostname lynx.你的域名.com"
    exit 0
}

if (-not $Setup) { Write-Host "用法见文件头部注释。"; exit 0 }
if (-not $Hostname) { throw "-Setup 需要 -Hostname，例如 -Hostname lynx.example.com" }

# 1) 创建隧道（已存在则复用）
$existing = & $exe tunnel list 2>$null | Select-String -Pattern "\s$TunnelName\s"
if (-not $existing) {
    & $exe tunnel create $TunnelName
} else {
    Write-Host "隧道 $TunnelName 已存在，复用。"
}

# 2) 写配置：只把本机 8001 暴露给这一个主机名，其余一律 404
$cfgDir = Join-Path $env:USERPROFILE ".cloudflared"
$uuid = (& $exe tunnel list --output json | ConvertFrom-Json | Where-Object { $_.name -eq $TunnelName }).id
if (-not $uuid) { throw "取不到隧道 ID" }
$cfg = @"
tunnel: $uuid
credentials-file: $cfgDir\$uuid.json
ingress:
  - hostname: $Hostname
    service: http://127.0.0.1:$Port
  - service: http_status:404
"@
# 用隧道名做文件名，不写公共的 config.yml：同一台机器上别的项目的 cloudflared
# （不带 --config 启动的那种）读的正是 $cfgDir\config.yml，覆盖它会直接切断对方的隧道。
$cfgPath = Join-Path $cfgDir "$TunnelName.yml"
$cfg | Out-File -FilePath $cfgPath -Encoding utf8 -Force
Write-Host "配置已写入 $cfgPath"

# 3) DNS：把 $Hostname 指向隧道（幂等）
#
# 必须显式带 --config 和 UUID：不带的话 cloudflared 读默认 profile 里的 config.yml，
# 用那份配置里的 tunnel 覆盖命令行传的名字。2026-08-25 踩过：CNAME 被指到了同机
# 另一个项目的隧道上，命令还报「Added CNAME」看不出错。
& $exe --config $cfgPath tunnel route dns --overwrite-dns $uuid $Hostname

# 4) 装成 Windows 服务（开机自启，机器重启也在线）
#
# 服务跑在 SYSTEM 账户下，读的是 SYSTEM profile 里的 config.yml，不是上面写的
# $env:USERPROFILE 那份。2026-08-25 踩过：这里只 Restart-Service 而不同步配置，
# 脚本报「已上线」但服务照旧跑着旧隧道（当时指向的还是另一个项目的 CRM）。
# 所以服务已存在时必须把配置和凭据一起同步过去再重启。
$sysCfgDir = "C:\Windows\System32\config\systemprofile\.cloudflared"
$svc = Get-Service -Name "cloudflared" -ErrorAction SilentlyContinue
if ($svc) {
    Write-Host "cloudflared 服务已存在，同步配置到 SYSTEM 账户后重启。"
    New-Item -ItemType Directory -Force -Path $sysCfgDir | Out-Null
    Copy-Item "$cfgDir\$uuid.json" "$sysCfgDir\$uuid.json" -Force
    $sysCfg = $cfg -replace [regex]::Escape("$cfgDir\$uuid.json"), "$sysCfgDir\$uuid.json"
    $sysCfg | Out-File -FilePath (Join-Path $sysCfgDir "config.yml") -Encoding utf8 -Force
    Restart-Service cloudflared
} else {
    & $exe --config $cfgPath service install
    Start-Service cloudflared
}

Write-Host ""
Write-Host "=== 已上线：https://$Hostname ==="
Write-Host ""
Write-Host "最后一步（邀请制，必须做，否则任何人都能看到登录页）："
Write-Host "  1. 打开 https://one.dash.cloudflare.com/ -> Access -> Applications -> Add an application"
Write-Host "  2. 类型选 Self-hosted，Application domain 填 $Hostname"
Write-Host "  3. Policy: Action=Allow, Include=Emails -> 填你和要邀请的人的邮箱"
Write-Host "  4. 保存。之后访问会先要邮箱验证码，通过后才看得到应用登录页。"
