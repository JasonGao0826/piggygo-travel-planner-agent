# 一键启动旅行规划助手：分别在两个窗口里启动后端和网页，都就绪后打开浏览器。
# 由仓库根目录的「启动.bat」调用；也可以直接运行：powershell -ExecutionPolicy Bypass -File scripts\start.ps1

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$api = Join-Path $root "services\api"
$web = Join-Path $root "apps\web"
$python = Join-Path $api ".venv\Scripts\python.exe"
$apiPort = 8765
$webPort = 3000

function Test-Port($port) {
    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $client.Connect("127.0.0.1", $port)
        $client.Close()
        return $true
    } catch {
        return $false
    }
}

function Stop-WithMessage($text) {
    Write-Host ""
    Write-Host $text -ForegroundColor Red
    Write-Host ""
    Read-Host "按回车键关闭"
    exit 1
}

Write-Host "旅行规划助手：正在启动……" -ForegroundColor Cyan

# ---------- 检查第一次安装是否完成 ----------
if (-not (Test-Path $python)) {
    Stop-WithMessage "还没有安装后端环境。请先按 services\api\README.md 的「运行测试」一节建好虚拟环境。"
}
if (-not (Test-Path (Join-Path $web "node_modules"))) {
    Stop-WithMessage "还没有安装网页依赖。请先在 apps\web 目录运行 npm ci（见 apps\web\README.md）。"
}
if (-not (Test-Path (Join-Path $api ".env"))) {
    Write-Host "提醒：没有找到 services\api\.env，对话和生成行程需要的模型 key 还没配置。其他功能可以正常使用。" -ForegroundColor Yellow
}

# ---------- 启动后端 ----------
if (Test-Port $apiPort) {
    Write-Host "后端已经在运行（端口 $apiPort），跳过。"
} else {
    $cmd = "`$Host.UI.RawUI.WindowTitle = '旅行规划助手 - 后端（关掉这个窗口就停止）'; " +
           "Set-Location -LiteralPath '$api'; & '$python' -m uvicorn tripagent.api.main:app --port $apiPort"
    Start-Process powershell -ArgumentList "-NoExit", "-NoProfile", "-Command", $cmd
    Write-Host "已在新窗口启动后端。"
}

# ---------- 启动网页 ----------
if (Test-Port $webPort) {
    Write-Host "网页已经在运行（端口 $webPort），跳过。"
} else {
    $cmd = "`$Host.UI.RawUI.WindowTitle = '旅行规划助手 - 网页（关掉这个窗口就停止）'; " +
           "Set-Location -LiteralPath '$web'; npm run dev"
    Start-Process powershell -ArgumentList "-NoExit", "-NoProfile", "-Command", $cmd
    Write-Host "已在新窗口启动网页。"
}

# ---------- 等两边都就绪，再打开浏览器 ----------
Write-Host "等待启动完成（第一次打开网页可能要半分钟）……"
$deadline = (Get-Date).AddSeconds(90)
while (-not ((Test-Port $apiPort) -and (Test-Port $webPort))) {
    if ((Get-Date) -gt $deadline) {
        Stop-WithMessage "90 秒内没有启动完成。请看后端和网页两个窗口里的报错信息。"
    }
    Start-Sleep -Seconds 1
}

Start-Process "http://localhost:$webPort"
Write-Host ""
Write-Host "已打开 http://localhost:$webPort 。" -ForegroundColor Green
Write-Host "要停止的话，关掉「后端」和「网页」两个窗口即可。这个窗口 5 秒后自动关闭。"
Start-Sleep -Seconds 5
