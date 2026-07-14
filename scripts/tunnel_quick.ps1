# 临时隧道（无域名过渡方案）：开机自启 + 把公网地址写到文件。
#
# trycloudflare 的地址每次启动都会变，所以启动后把它抓出来写进 runtime\PUBLIC_URL.txt，
# 需要时看这个文件即可。有了自己的域名后改用 deploy_cloudflare.ps1（地址固定、开机自启、
# 可配 Access 邮箱白名单），这个脚本就可以停掉。
#
# 由计划任务 "LynxAgentTunnel" 每分钟触发：隧道活着就直接退出，死了才重开（同看门狗思路，
# 一次性脚本不常驻，避免挂死实例卡住后续触发）。

$ErrorActionPreference = "Continue"
$root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $root

$exe = Join-Path $root "runtime\cloudflared\cloudflared.exe"
$log = Join-Path $root "runtime\cloudflared\quick.log"
$urlFile = Join-Path $root "runtime\PUBLIC_URL.txt"

# 已在运行 -> 什么都不做
if (Get-Process cloudflared -ErrorAction SilentlyContinue) { exit 0 }

# 后端没起来就先别开隧道（否则对外是 502）
$backend = Get-NetTCPConnection -LocalPort 8001 -State Listen -ErrorAction SilentlyContinue
if (-not $backend) { exit 0 }

Remove-Item $log -ErrorAction SilentlyContinue
Start-Process -FilePath $exe `
    -ArgumentList "tunnel", "--url", "http://127.0.0.1:8001", "--no-autoupdate" `
    -RedirectStandardOutput $log -RedirectStandardError "$log.err" `
    -WindowStyle Hidden

# 抓取本次分配的公网地址（cloudflared 启动后几秒内打印）
for ($i = 0; $i -lt 20; $i++) {
    Start-Sleep -Seconds 2
    $hit = Select-String -Path $log, "$log.err" -Pattern "https://[a-z0-9-]+\.trycloudflare\.com" -ErrorAction SilentlyContinue |
           Select-Object -First 1
    if ($hit) {
        $url = [regex]::Match($hit.Line, "https://[a-z0-9-]+\.trycloudflare\.com").Value
        # 纯 ASCII 无 BOM：PS 5.1 的 -Encoding utf8 会写 BOM，脚本/curl 读到会连地址一起吃掉
        [System.IO.File]::WriteAllText($urlFile, $url, (New-Object System.Text.UTF8Encoding($false)))
        "[$(Get-Date -Format o)] $url" | Out-File -FilePath (Join-Path $root "runtime\cloudflared\url_history.log") -Append -Encoding utf8
        Write-Output "公网地址: $url  (已写入 runtime\PUBLIC_URL.txt)"
        exit 0
    }
}
Write-Output "未能取到公网地址，见 $log"
