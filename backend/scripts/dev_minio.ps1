<#
.SYNOPSIS
  智印助手本地开发用 MinIO 管理脚本（Windows 免安装二进制版）。

.DESCRIPTION
  MinIO 默认放在 backend\.tools\minio\minio.exe，数据目录默认 backend\.data\minio。
  可通过 ZHIYIN_MINIO_EXE / ZHIYIN_MINIO_DATA 覆盖。
  账号密码从 backend/.env 的 MINIO_ACCESS_KEY / MINIO_SECRET_KEY 读取。

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\dev_minio.ps1 start
  powershell -ExecutionPolicy Bypass -File scripts\dev_minio.ps1 bucket
#>
param(
  [Parameter(Mandatory = $true)]
  [ValidateSet('start', 'stop', 'status', 'bucket')]
  [string]$Action,

  [string]$MinioExe = '',
  [string]$DataDir = '',
  [int]$Port = 9000,
  [int]$ConsolePort = 9001,
  [string]$AccessKey = '',
  [string]$SecretKey = '',
  [string]$Bucket = 'zhiyin-files'
)

$ErrorActionPreference = 'Stop'
$backendDir = Split-Path -Parent $PSScriptRoot

if (-not $MinioExe) {
  $MinioExe = if ($env:ZHIYIN_MINIO_EXE) { $env:ZHIYIN_MINIO_EXE } else { Join-Path $backendDir '.tools\minio\minio.exe' }
}
if (-not $DataDir) {
  $DataDir = if ($env:ZHIYIN_MINIO_DATA) { $env:ZHIYIN_MINIO_DATA } else { Join-Path $backendDir '.data\minio' }
}

function Read-EnvValue([string]$key) {
  $envFile = Join-Path $backendDir '.env'
  if (-not (Test-Path -LiteralPath $envFile)) { return '' }
  $line = Get-Content -LiteralPath $envFile -Encoding UTF8 |
    Where-Object { $_ -match "^\s*$key\s*=" } |
    Select-Object -Last 1
  if (-not $line) { return '' }
  return ($line -split '=', 2)[1].Trim()
}

if (-not $AccessKey) {
  $AccessKey = if ($env:MINIO_ACCESS_KEY) { $env:MINIO_ACCESS_KEY } else { Read-EnvValue 'MINIO_ACCESS_KEY' }
}
if (-not $SecretKey) {
  $SecretKey = if ($env:MINIO_SECRET_KEY) { $env:MINIO_SECRET_KEY } else { Read-EnvValue 'MINIO_SECRET_KEY' }
}
if (-not $AccessKey -or -not $SecretKey) {
  throw "未配置 MinIO 账号：请先复制 backend/.env.example 为 backend/.env 并填写 MINIO_ACCESS_KEY / MINIO_SECRET_KEY"
}

$logFile = Join-Path $DataDir 'minio.log'

function Test-ServerUp {
  $client = New-Object System.Net.Sockets.TcpClient
  try {
    $task = $client.ConnectAsync('127.0.0.1', $Port)
    return $task.Wait(800) -and $client.Connected
  } catch {
    return $false
  } finally {
    $client.Close()
  }
}

switch ($Action) {
  'start' {
    if (-not (Test-Path -LiteralPath $MinioExe)) { throw "未找到 MinIO 可执行文件：$MinioExe" }
    if (Test-ServerUp) { Write-Host "MinIO 已在 $Port 端口运行，无需重复启动"; break }
    New-Item -ItemType Directory -Path $DataDir -Force | Out-Null

    # MinIO 通过环境变量读取初始账号密码
    $env:MINIO_ROOT_USER = $AccessKey
    $env:MINIO_ROOT_PASSWORD = $SecretKey

    Write-Host "正在启动 MinIO（数据目录：$DataDir）..."
    $cmdLine = 'start "zhiyin-minio" /b "' + $MinioExe + '" server "' + $DataDir +
               '" --address ":' + $Port + '" --console-address ":' + $ConsolePort +
               '" > "' + $logFile + '" 2>&1'
    & cmd.exe /c $cmdLine

    for ($i = 1; $i -le 20; $i++) {
      Start-Sleep -Seconds 1
      if (Test-ServerUp) { break }
    }
    if (Test-ServerUp) {
      Write-Host "MinIO 已启动：API http://127.0.0.1:$Port  控制台 http://127.0.0.1:$ConsolePort"
      Write-Host "日志：$logFile"
    } else {
      throw "MinIO 启动超时，请查看日志：$logFile"
    }
  }

  'stop' {
    $procs = Get-CimInstance Win32_Process -Filter "Name='minio.exe'" -ErrorAction SilentlyContinue
    if (-not $procs) { Write-Host 'MinIO 未在运行'; break }
    foreach ($proc in $procs) { Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue }
    Write-Host "MinIO 已停止（$($procs.Count) 个进程）"
  }

  'status' {
    if (Test-ServerUp) { Write-Host "MinIO 正在运行，端口 $Port" }
    else { Write-Host "MinIO 未运行（数据目录：$DataDir）" }
  }

  'bucket' {
    if (-not (Test-ServerUp)) { throw 'MinIO 未运行，请先执行 start' }
    $python = Join-Path $backendDir '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $python)) { throw "未找到 venv python：$python" }
    $code = "from app.services.storage import get_storage; get_storage().ensure_bucket(); print('bucket ready')"
    Push-Location $backendDir
    try {
      & $python -c $code
      if ($LASTEXITCODE -ne 0) { throw "创建桶失败：$Bucket" }
    } finally {
      Pop-Location
    }
    Write-Host "私有桶已就绪：$Bucket"
  }
}
