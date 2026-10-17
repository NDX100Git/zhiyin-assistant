<#
.SYNOPSIS
  智印助手本地开发用 MySQL 8（免安装版）管理脚本。

.DESCRIPTION
  免安装版 MySQL 默认解压在 backend\.tools\mysql8，数据目录为其下的 data。
  可通过环境变量 ZHIYIN_MYSQL_ROOT 指定其他位置。
  仅用于本机开发演示，不涉及任何线上数据。

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\dev_mysql.ps1 init
  powershell -ExecutionPolicy Bypass -File scripts\dev_mysql.ps1 start
  powershell -ExecutionPolicy Bypass -File scripts\dev_mysql.ps1 create-db
#>
param(
  [Parameter(Mandatory = $true)]
  [ValidateSet('init', 'start', 'stop', 'status', 'create-db', 'apply-schema', 'shell')]
  [string]$Action,

  [string]$MysqlRoot = '',
  [int]$Port = 3306,
  [string]$RootPassword = '',
  [string]$Database = 'zhiyin'
)

$ErrorActionPreference = 'Stop'

$backendDir = Split-Path -Parent $PSScriptRoot
if (-not $MysqlRoot) {
  $MysqlRoot = if ($env:ZHIYIN_MYSQL_ROOT) { $env:ZHIYIN_MYSQL_ROOT } else { Join-Path $backendDir '.tools\mysql8' }
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

if (-not $RootPassword) {
  $RootPassword = if ($env:DB_PASSWORD) { $env:DB_PASSWORD } else { Read-EnvValue 'DB_PASSWORD' }
}
if (-not $RootPassword -and $Action -in @('create-db', 'stop', 'apply-schema', 'shell')) {
  throw "未配置 DB_PASSWORD：请先复制 backend/.env.example 为 backend/.env 并填写 DB_PASSWORD"
}

$dataDir    = Join-Path $MysqlRoot 'data'
$logDir     = Join-Path $MysqlRoot 'logs'
$mysqld     = Join-Path $MysqlRoot 'bin\mysqld.exe'
$mysql      = Join-Path $MysqlRoot 'bin\mysql.exe'
$mysqladmin = Join-Path $MysqlRoot 'bin\mysqladmin.exe'
$schemaFile = Join-Path (Split-Path -Parent $PSScriptRoot) 'schema.sql'

function Assert-MysqlRoot {
  if (-not (Test-Path -LiteralPath $mysqld)) {
    throw "未找到 $mysqld，请确认免安装版 MySQL 已解压到 $MysqlRoot"
  }
}

function Test-ServerUp {
  # 直接尝试 TCP 连接，比查询端口监听状态更可靠（部分受限环境查不到监听表）
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
  'init' {
    Assert-MysqlRoot
    if (Test-Path -LiteralPath $dataDir) {
      Write-Host "数据目录已存在，跳过初始化: $dataDir"
      break
    }
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
    Write-Host '正在初始化数据目录（root 账号暂无密码）...'
    & $mysqld --initialize-insecure --basedir=$MysqlRoot --datadir=$dataDir
    if ($LASTEXITCODE -ne 0) { throw 'MySQL 初始化失败' }
    Write-Host '初始化完成。下一步执行 start，然后执行 create-db 设置 root 密码并建库。'
  }

  'start' {
    Assert-MysqlRoot
    if (Test-ServerUp) {
      Write-Host "MySQL 已在 $Port 端口运行，无需重复启动"
      break
    }
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
    $stderr = Join-Path $logDir 'mysqld.err.log'
    Write-Host "正在启动 MySQL（数据目录：$dataDir）..."
    # 用 cmd 的 start /b 后台拉起，避免 PowerShell 5.1 在存在 Path/PATH 重复键时 Start-Process 报错
    $cmdLine = 'start "zhiyin-mysql" /b "' + $mysqld + '" --basedir="' + $MysqlRoot +
               '" --datadir="' + $dataDir + '" --port=' + $Port + ' --log-error="' + $stderr + '"'
    # 必须把 cmd 的输出重定向掉，否则 mysqld 会占住管道导致脚本一直等待
    & cmd.exe /c ($cmdLine + ' > NUL 2>&1')

    for ($i = 1; $i -le 30; $i++) {
      Start-Sleep -Seconds 1
      if (Test-ServerUp) { break }
    }
    if (Test-ServerUp) {
      Write-Host "MySQL 已启动，端口 $Port（日志：$stderr）"
    } else {
      throw "MySQL 启动超时，请查看日志：$stderr"
    }
  }

  'stop' {
    Assert-MysqlRoot
    if (-not (Test-ServerUp)) {
      Write-Host "MySQL 未在 $Port 端口运行"
      break
    }
    & $mysqladmin --host=127.0.0.1 --port=$Port --user=root "--password=$RootPassword" shutdown
    Write-Host 'MySQL 已停止'
  }

  'status' {
    if (Test-ServerUp) {
      Write-Host "MySQL 正在运行，端口 $Port"
    } else {
      Write-Host "MySQL 未运行（数据目录：$dataDir）"
    }
  }

  'create-db' {
    Assert-MysqlRoot
    if (-not (Test-ServerUp)) { throw "MySQL 未运行，请先执行 start" }

    # 判断 root 是否已设置密码：无密码可连则先设置密码
    $noPwdOk = $false
    try {
      & $mysql --host=127.0.0.1 --port=$Port --user=root -e 'SELECT 1;' 2>$null | Out-Null
      $noPwdOk = ($LASTEXITCODE -eq 0)
    } catch { $noPwdOk = $false }

    if ($noPwdOk) {
      Write-Host '检测到 root 无密码，正在设置密码...'
      & $mysql --host=127.0.0.1 --port=$Port --user=root `
        -e "ALTER USER 'root'@'localhost' IDENTIFIED BY '$RootPassword'; FLUSH PRIVILEGES;"
      if ($LASTEXITCODE -ne 0) { throw '设置 root 密码失败' }
    }

    & $mysql --host=127.0.0.1 --port=$Port --user=root "--password=$RootPassword" `
      -e "CREATE DATABASE IF NOT EXISTS ``$Database`` DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
    if ($LASTEXITCODE -ne 0) { throw "创建数据库 $Database 失败" }
    Write-Host "数据库已就绪：$Database"
  }

  'apply-schema' {
    Assert-MysqlRoot
    if (-not (Test-Path -LiteralPath $schemaFile)) { throw "未找到建表脚本：$schemaFile" }
    Get-Content -LiteralPath $schemaFile -Raw -Encoding UTF8 |
      & $mysql --host=127.0.0.1 --port=$Port --user=root "--password=$RootPassword" --default-character-set=utf8mb4
    if ($LASTEXITCODE -ne 0) { throw '执行 schema.sql 失败' }
    Write-Host '建表脚本执行完成'
  }

  'shell' {
    Assert-MysqlRoot
    & $mysql --host=127.0.0.1 --port=$Port --user=root "--password=$RootPassword" --default-character-set=utf8mb4 $Database
  }
}
