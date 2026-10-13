<#
.SYNOPSIS
    合同审批智能审查系统 —— 一键停止：后端 + 前端（可选连 MySQL 容器一起停）

.DESCRIPTION
    只杀进程 / 停容器，不删任何文件、不删数据卷。
    默认会一并把 MySQL 容器停掉（数据保留在命名卷 xm_contract_mysql_data 里），
    想留着库不停就加 -KeepDb。

.PARAMETER KeepDb
    不停 MySQL 容器，只关后端和前端。

.PARAMETER RemoveContainers
    用 docker compose down 删掉容器（数据卷仍保留）。比 -KeepDb 更彻底，但仍不动数据。

.EXAMPLE
    .\scripts\stop_all.ps1
    .\scripts\stop_all.ps1 -KeepDb
#>
[CmdletBinding()]
param(
    [switch]$KeepDb,
    [switch]$RemoveContainers
)

$ErrorActionPreference = 'Continue'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }
$OutputEncoding = [System.Text.Encoding]::UTF8

function Write-Title([string]$Text) {
    Write-Host ''
    Write-Host ('=' * 66) -ForegroundColor DarkCyan
    Write-Host "  $Text" -ForegroundColor Cyan
    Write-Host ('=' * 66) -ForegroundColor DarkCyan
}
function Write-Step([string]$Text) { Write-Host ''; Write-Host ">> $Text" -ForegroundColor Cyan }
function Write-Ok([string]$Text)   { Write-Host "   [OK] $Text" -ForegroundColor Green }
function Write-Warn2([string]$Text){ Write-Host "   [!!] $Text" -ForegroundColor Yellow }
function Write-Info([string]$Text) { Write-Host "        $Text" -ForegroundColor Gray }

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$EnvFile     = Join-Path $ProjectRoot '.env'
$FrontCandidates = @(5173, 5174, 5175, 5176)

# ---- 读 .env 里的后端端口 ----
$appPort = 8000
$dbPort  = 3307
if (Test-Path -LiteralPath $EnvFile) {
    foreach ($line in (Get-Content -LiteralPath $EnvFile -Encoding UTF8 -ErrorAction SilentlyContinue)) {
        if ($line -match '^\s*#') { continue }   # 跳过注释行，否则被注释的备用配置会覆盖真实值
        if ($line -match '^\s*APP_PORT\s*=\s*(\d+)') { $appPort = [int]$Matches[1] }
        if ($line -match '@[^:@/]+:(\d+)/')          { $dbPort  = [int]$Matches[1] }
    }
}

# 端口探测：IPv4 与 IPv6 都要试
#   （vite 默认只监听 [::1]:5173；.NET 的 TcpClient 默认 IPv4 socket，须按地址族分别建）
function Test-TcpPort {
    param([int]$Port, [int]$TimeoutMs = 400)
    foreach ($addr in @('127.0.0.1', '::1')) {
        $family = [System.Net.Sockets.AddressFamily]::InterNetwork
        if ($addr.Contains(':')) { $family = [System.Net.Sockets.AddressFamily]::InterNetworkV6 }
        $client = $null
        try {
            $client = [System.Net.Sockets.TcpClient]::new($family)
            $iar = $client.BeginConnect($addr, $Port, $null, $null)
            if ($iar.AsyncWaitHandle.WaitOne($TimeoutMs, $false)) {
                $client.EndConnect($iar)
                $client.Close()
                return $true
            }
        } catch {
        } finally {
            if ($client) { $client.Close() }
        }
    }
    return $false
}

function Get-ListenerPid {
    param([int]$Port)
    try {
        $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop |
                Select-Object -First 1
        if ($conn) { return [int]$conn.OwningProcess }
    } catch { }
    try {
        $line = netstat -ano | Select-String -Pattern ":$Port\s+\S+\s+LISTENING\s+(\d+)" |
                Select-Object -First 1
        if ($line -and $line.Matches.Count -gt 0) { return [int]$line.Matches[0].Groups[1].Value }
    } catch { }
    return 0
}

# 连带子进程一起杀（uvicorn --reload 是父+子两层，只杀子进程父进程会立刻重启它）
function Stop-ProcessTree {
    param([int]$ProcessId)
    try {
        $children = Get-CimInstance Win32_Process -Filter "ParentProcessId=$ProcessId" -ErrorAction SilentlyContinue
        foreach ($c in $children) { Stop-ProcessTree -ProcessId ([int]$c.ProcessId) }
    } catch { }
    try {
        $proc = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
        if ($proc) {
            Write-Ok ("结束进程 {0} ({1})" -f $ProcessId, $proc.ProcessName)
            Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
        }
    } catch { }
}

Write-Title '合同审批智能审查系统 · 一键停止'
Write-Info "项目根 : $ProjectRoot"

$killed = 0

# ---- 1. 按端口杀（后端 + 前端）----
Write-Step '关闭后端 / 前端'
$targets = @()
$targets += [pscustomobject]@{ Name = '后端'; Port = $appPort }
foreach ($p in $FrontCandidates) { $targets += [pscustomobject]@{ Name = '前端'; Port = $p } }

foreach ($t in $targets) {
    if (Test-TcpPort -Port $t.Port -TimeoutMs 300) {
        $owner = Get-ListenerPid -Port $t.Port
        if ($owner -gt 0) {
            Write-Info "$($t.Name) 端口 $($t.Port) → PID $owner"
            Stop-ProcessTree -ProcessId $owner
            $killed++
        } else {
            Write-Warn2 "$($t.Name) 端口 $($t.Port) 被占用，但查不到 PID，跳过"
        }
    }
}
if ($killed -eq 0) { Write-Ok '没有端口被占用（后端 / 前端本来就没在跑）' }

# ---- 2. 兜底：清掉本项目的 uvicorn / vite 残留进程 ----
Write-Step '清理残留进程（本项目路径下的 uvicorn / vite）'
$leftover = @()
try {
    $leftover = Get-CimInstance Win32_Process -ErrorAction Stop | Where-Object {
        $_.CommandLine -and
        $_.CommandLine -like "*$ProjectRoot*" -and
        ($_.CommandLine -match 'uvicorn' -or $_.CommandLine -match 'vite')
    }
} catch { }

if ($leftover -and @($leftover).Count -gt 0) {
    foreach ($p in $leftover) {
        Stop-ProcessTree -ProcessId ([int]$p.ProcessId)
        $killed++
    }
    Start-Sleep -Milliseconds 500
} else {
    Write-Ok '没有残留'
}

# ---- 3. MySQL 容器 ----
if ($KeepDb) {
    Write-Step '跳过数据库（-KeepDb）'
    if (Test-TcpPort -Port $dbPort -TimeoutMs 300) { Write-Info "MySQL 仍在 $dbPort 上运行" }
} else {
    Write-Step '停数据库容器'
    $dockerExe = Get-Command docker.exe -ErrorAction SilentlyContinue
    $dockerPath = ''
    if ($dockerExe) { $dockerPath = $dockerExe.Source }
    elseif (Test-Path -LiteralPath (Join-Path $env:ProgramFiles 'Docker\Docker\resources\bin\docker.exe')) {
        $dockerPath = Join-Path $env:ProgramFiles 'Docker\Docker\resources\bin\docker.exe'
    }

    if (-not $dockerPath) {
        Write-Warn2 '没找到 docker 命令，跳过'
    } else {
        Push-Location $ProjectRoot
        if ($RemoveContainers) {
            & $dockerPath compose down
            Write-Ok 'docker compose down 完成（容器已删，数据卷 xm_contract_mysql_data 保留）'
        } else {
            & $dockerPath compose stop mysql
            Write-Ok 'MySQL 容器已停（数据保留在数据卷里）'
        }
        Pop-Location
    }
}

# ---- 4. 收尾核对 ----
Write-Step '收尾核对'
$stillOpen = @()
if (Test-TcpPort -Port $appPort -TimeoutMs 300) { $stillOpen += "后端 $appPort" }
foreach ($p in $FrontCandidates) { if (Test-TcpPort -Port $p -TimeoutMs 300) { $stillOpen += "前端 $p" } }
if (-not $KeepDb -and (Test-TcpPort -Port $dbPort -TimeoutMs 300)) { $stillOpen += "MySQL $dbPort" }

if ($stillOpen.Count -eq 0) {
    Write-Ok '全部已关闭'
} else {
    Write-Warn2 ('仍在监听：' + ($stillOpen -join '、') + '  —— 可能是别的程序占着这个端口，不是本项目的')
}

Write-Host ''
Write-Host '   重新启动：双击 start-all.bat' -ForegroundColor Gray
Write-Host '   注意　　：关掉服务不会删数据；只有 docker compose down -v 才会连库一起清' -ForegroundColor Gray
Write-Host ('=' * 66) -ForegroundColor DarkCyan
Write-Host ''
