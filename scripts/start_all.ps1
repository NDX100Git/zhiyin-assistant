# 智印助手一键启动：MySQL + MinIO + 后端 + 学生端H5 + 老师端Web
# 用法：双击项目根目录的 start_all.cmd

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$asciiTmp = Join-Path $root '.runtime\tmp'
if ($asciiTmp -match '[^\x00-\x7F]') {
  # 项目路径含中文时，esbuild / LibreOffice 可能无法处理临时目录，回退到系统盘英文目录
  $asciiTmp = Join-Path $env:SystemRoot 'Temp\zhiyin'
}
if (-not (Test-Path $asciiTmp)) { New-Item -ItemType Directory -Path $asciiTmp -Force | Out-Null }
$env:TMP = $asciiTmp
$env:TEMP = $asciiTmp

function Test-Port([int]$port) {
  $client = New-Object System.Net.Sockets.TcpClient
  try {
    $task = $client.ConnectAsync('127.0.0.1', $port)
    return $task.Wait(600) -and $client.Connected
  } catch {
    return $false
  } finally {
    $client.Close()
  }
}

Write-Host '== 启动 MySQL ==' -ForegroundColor Cyan
& powershell -NoProfile -ExecutionPolicy Bypass -File "$root\backend\scripts\dev_mysql.ps1" start

Write-Host '== 启动 MinIO ==' -ForegroundColor Cyan
& powershell -NoProfile -ExecutionPolicy Bypass -File "$root\backend\scripts\dev_minio.ps1" start
& powershell -NoProfile -ExecutionPolicy Bypass -File "$root\backend\scripts\dev_minio.ps1" bucket

if (-not (Test-Port 8000)) {
  Write-Host '== 启动后端 API :8000 ==' -ForegroundColor Cyan
  Start-Process -FilePath "$root\backend\.venv\Scripts\python.exe" `
    -ArgumentList '-m','uvicorn','app.main:app','--host','0.0.0.0','--port','8000' `
    -WorkingDirectory "$root\backend" `
    -RedirectStandardOutput "$asciiTmp\uvicorn.out.log" `
    -RedirectStandardError "$asciiTmp\uvicorn.err.log" `
    -WindowStyle Hidden | Out-Null
} else {
  Write-Host '后端 8000 已在运行，跳过启动'
}

if (-not (Test-Port 5173)) {
  Write-Host '== 启动学生端 H5 :5173 ==' -ForegroundColor Cyan
  Start-Process -FilePath 'cmd.exe' -ArgumentList '/c','npm.cmd run dev:h5' `
    -WorkingDirectory "$root\student-app" `
    -RedirectStandardOutput "$asciiTmp\h5-dev.log" `
    -RedirectStandardError "$asciiTmp\h5-dev.err.log" `
    -WindowStyle Hidden | Out-Null
} else {
  Write-Host '学生端 5173 已在运行，跳过启动'
}

if (-not (Test-Port 5174)) {
  Write-Host '== 启动老师端 :5174 ==' -ForegroundColor Cyan
  Start-Process -FilePath 'cmd.exe' -ArgumentList '/c','npm.cmd run dev' `
    -WorkingDirectory "$root\teacher-web" `
    -RedirectStandardOutput "$asciiTmp\teacher-dev.log" `
    -RedirectStandardError "$asciiTmp\teacher-dev.err.log" `
    -WindowStyle Hidden | Out-Null
} else {
  Write-Host '老师端 5174 已在运行，跳过启动'
}

Write-Host '等待服务就绪...'
Start-Sleep -Seconds 10

$apiOk = $false
try {
  $health = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/health' -TimeoutSec 10
  $apiOk = ($health.code -eq 0)
} catch {
  $apiOk = $false
}

Write-Host ''
Write-Host '================================' -ForegroundColor Green
if ($apiOk) {
  Write-Host '后端：   http://127.0.0.1:8000/docs' -ForegroundColor Green
} else {
  Write-Host "后端未就绪，请查看 $asciiTmp\uvicorn.err.log" -ForegroundColor Yellow
}
Write-Host '学生端： http://127.0.0.1:5173'
Write-Host '老师端： http://127.0.0.1:5174  （账号密码见 backend/.env）'
Write-Host '================================' -ForegroundColor Green
Write-Host '关闭服务：关闭对应的后台进程，或重启电脑即可。'

Start-Process 'http://127.0.0.1:5173'
Start-Process 'http://127.0.0.1:5174'
