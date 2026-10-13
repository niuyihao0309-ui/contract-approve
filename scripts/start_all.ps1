<#
.SYNOPSIS
    合同审批智能审查系统 —— 一键启动：Docker MySQL(3307) + 后端(8000) + 前端(5173)

.DESCRIPTION
    只做「拉起 / 等待 / 报错」，不改动项目里任何业务代码，也不在项目里写业务文件。
    每个服务各开一个独立窗口，日志直接看窗口；脚本本身幂等 —— 已在跑的服务会跳过而不是重复起。

.PARAMETER CheckOnly
    只体检不动手：检查 venv / .env / node_modules / Docker / 端口占用，然后退出。

.PARAMETER NoBrowser
    启动完成后不自动打开浏览器。

.PARAMETER SkipDocker
    跳过 Docker 步骤（复用你自己那套 MySQL 时用）。

.PARAMETER SkipBackend / SkipFrontend
    只起其中一半。

.PARAMETER NoDockerDesktop
    Docker 守护进程没起来时不尝试拉起 Docker Desktop，只报错。

.PARAMETER BackendPort
    覆盖 .env 里的 APP_PORT。

.PARAMETER TimeoutSeconds
    等依赖就绪的上限秒数（默认 180）。

.EXAMPLE
    .\scripts\start_all.ps1
    .\scripts\start_all.ps1 -CheckOnly
    .\scripts\start_all.ps1 -NoBrowser -SkipDocker
#>
[CmdletBinding()]
param(
    [switch]$CheckOnly,
    [switch]$NoBrowser,
    [switch]$SkipDocker,
    [switch]$SkipBackend,
    [switch]$SkipFrontend,
    [switch]$NoDockerDesktop,
    [int]$BackendPort = 0,
    [int]$TimeoutSeconds = 180
)

$ErrorActionPreference = 'Continue'

# ---------------- 控制台编码：中文不乱码 ----------------
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }
$OutputEncoding = [System.Text.Encoding]::UTF8

# ---------------- 输出小工具 ----------------
function Write-Title([string]$Text) {
    Write-Host ''
    Write-Host ('=' * 66) -ForegroundColor DarkCyan
    Write-Host "  $Text" -ForegroundColor Cyan
    Write-Host ('=' * 66) -ForegroundColor DarkCyan
}
function Write-Step([string]$Text) { Write-Host ''; Write-Host ">> $Text" -ForegroundColor Cyan }
function Write-Ok([string]$Text)   { Write-Host "   [OK] $Text" -ForegroundColor Green }
function Write-Warn2([string]$Text){ Write-Host "   [!!] $Text" -ForegroundColor Yellow }
function Write-Err([string]$Text)  { Write-Host "   [XX] $Text" -ForegroundColor Red }
function Write-Info([string]$Text) { Write-Host "        $Text" -ForegroundColor Gray }

# ---------------- 定位项目根 ----------------
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$ComposeFile = Join-Path $ProjectRoot 'docker-compose.yml'
if (-not (Test-Path -LiteralPath $ComposeFile)) {
    Write-Err "在 '$ProjectRoot' 下找不到 docker-compose.yml，脚本位置不对（本文件应放在项目根的 scripts/ 目录里）。"
    exit 1
}

$EnvFile     = Join-Path $ProjectRoot '.env'
$EnvExample  = Join-Path $ProjectRoot '.env.example'
$VenvPy      = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$FrontendDir = Join-Path $ProjectRoot 'frontend'
$Problems    = New-Object System.Collections.ArrayList

# ---------------- 通用探测 ----------------
# 端口探测：IPv4 与 IPv6 都要试
#   —— vite 的 dev server 默认绑 localhost，在 Windows 上实际只监听 [::1]:5173，
#      只测 127.0.0.1 会误判成"没起来"；
#      而 .NET 的 TcpClient 默认是 IPv4 socket，所以必须按地址族分别建。
function Test-TcpPort {
    param([int]$Port, [int]$TimeoutMs = 500)
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

function Wait-TcpPort {
    param([int]$Port, [int]$Seconds = 60)
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-TcpPort -Port $Port -TimeoutMs 400) { return $true }
        Start-Sleep -Milliseconds 700
    }
    return $false
}

# 在候选端口里找出「已经有服务在监听」的那个（0 = 都没有）
function Get-ListeningPort {
    param([int[]]$Ports)
    foreach ($p in $Ports) { if (Test-TcpPort -Port $p -TimeoutMs 300) { return $p } }
    return 0
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

function Get-ExeOnPath {
    param([string]$Name)
    $c = Get-Command $Name -ErrorAction SilentlyContinue
    if ($c) { return $c.Source }
    return ''
}

# 找 npm.cmd：PATH 优先，其次系统 Node，其次 WorkBuddy 隔离版 Node
function Resolve-Npm {
    $cands = New-Object System.Collections.ArrayList
    $onPath = Get-ExeOnPath 'npm.cmd'
    if ($onPath) { [void]$cands.Add($onPath) }
    [void]$cands.Add((Join-Path $env:ProgramFiles 'nodejs\npm.cmd'))
    try {
        Get-ChildItem -Path (Join-Path $env:USERPROFILE '.workbuddy\binaries\node\versions') `
                      -Filter 'npm.cmd' -Recurse -Depth 2 -ErrorAction SilentlyContinue |
            ForEach-Object { [void]$cands.Add($_.FullName) }
    } catch { }
    foreach ($c in $cands) { if ($c -and (Test-Path -LiteralPath $c)) { return $c } }
    return ''
}

function Resolve-Node {
    $onPath = Get-ExeOnPath 'node.exe'
    if ($onPath) { return $onPath }
    $sys = Join-Path $env:ProgramFiles 'nodejs\node.exe'
    if (Test-Path -LiteralPath $sys) { return $sys }
    try {
        $hit = Get-ChildItem -Path (Join-Path $env:USERPROFILE '.workbuddy\binaries\node\versions') `
                             -Filter 'node.exe' -Recurse -Depth 2 -ErrorAction SilentlyContinue |
               Select-Object -First 1
        if ($hit) { return $hit.FullName }
    } catch { }
    return ''
}

function Resolve-Docker {
    $onPath = Get-ExeOnPath 'docker.exe'
    if ($onPath) { return $onPath }
    $sys = Join-Path $env:ProgramFiles 'Docker\Docker\resources\bin\docker.exe'
    if (Test-Path -LiteralPath $sys) { return $sys }
    return ''
}

# 当前用的到底是什么 shell（子窗口沿用同一个，保证行为一致）
$ShellExe = 'powershell.exe'
try {
    $p = (Get-Process -Id $PID).Path
    if ($p) { $ShellExe = $p }
} catch { }

Write-Title '合同审批智能审查系统 · 一键启动'
Write-Info "项目根 : $ProjectRoot"
Write-Info "时间   : $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
if ($CheckOnly) { Write-Info '模式   : 体检（-CheckOnly，不会启动任何服务）' }

# ================================================================
# 1. 读 .env（端口 / 数据库端口）
# ================================================================
$appHost = '127.0.0.1'
$appPort = 8000
$dbPort  = 3307
if (Test-Path -LiteralPath $EnvFile) {
    foreach ($line in (Get-Content -LiteralPath $EnvFile -Encoding UTF8 -ErrorAction SilentlyContinue)) {
        if ($line -match '^\s*#') { continue }   # 跳过注释行，否则被注释的备用配置会覆盖真实值
        if ($line -match '^\s*APP_PORT\s*=\s*(\d+)')       { $appPort = [int]$Matches[1] }
        if ($line -match '^\s*APP_HOST\s*=\s*([\w\.\-]+)') { $appHost = $Matches[1] }
        if ($line -match '@[^:@/]+:(\d+)/')                 { $dbPort  = [int]$Matches[1] }
    }
}
if ($BackendPort -gt 0) { $appPort = $BackendPort }
$apiBase  = "http://${appHost}:$appPort"
$frontCandidates = @(5173, 5174, 5175)
# 单个服务就绪的等待上限：默认 90s，传了更小的 -TimeoutSeconds 就按更小的来
$svcTimeout = [Math]::Min($TimeoutSeconds, 90)

# ================================================================
# 2. 环境体检
# ================================================================
Write-Step '环境体检'

if (Test-Path -LiteralPath $VenvPy) {
    Write-Ok '后端虚拟环境 .venv\Scripts\python.exe'
} else {
    Write-Warn2 '缺少 .venv（后端依赖没装）'
    [void]$Problems.Add('venv')
}

if (Test-Path -LiteralPath $EnvFile) {
    Write-Ok '.env 配置文件'
} elseif (Test-Path -LiteralPath $EnvExample) {
    Write-Warn2 '缺少 .env（可从 .env.example 生成）'
    [void]$Problems.Add('env')
} else {
    Write-Warn2 '缺少 .env 和 .env.example'
    [void]$Problems.Add('env')
}

$npmCmd  = Resolve-Npm
$nodeExe = Resolve-Node
if (Test-Path -LiteralPath (Join-Path $FrontendDir 'node_modules')) {
    Write-Ok '前端依赖 frontend\node_modules'
} else {
    Write-Warn2 '前端依赖没装（需 npm install）'
    [void]$Problems.Add('npm')
}
if ($npmCmd)      { Write-Ok "npm    : $npmCmd" }
elseif ($nodeExe) { Write-Warn2 "没找到 npm，但有 node，将直接用 vite：$nodeExe" }
else              { Write-Warn2 '没找到 npm / node，前端起不来'; [void]$Problems.Add('node') }

$dockerExe = Resolve-Docker
if ($SkipDocker) {
    Write-Info '已按参数跳过 Docker 检查'
} elseif ($dockerExe) {
    Write-Ok "docker : $dockerExe"
} else {
    Write-Warn2 '没找到 docker 命令（Docker Desktop 未安装或未在 PATH）'
    [void]$Problems.Add('docker')
}

# 端口现状
if (Test-TcpPort -Port $appPort -TimeoutMs 400) { Write-Info "端口 $appPort ：已被监听（后端可能已在运行）" }
else { Write-Info "端口 $appPort ：空闲" }
$busyFront = Get-ListeningPort -Ports $frontCandidates
if ($busyFront -gt 0) { Write-Info "端口 $busyFront ：已被监听（前端可能已在运行）" }
else { Write-Info "端口 5173/5174/5175 ：全部空闲" }
if (-not $SkipDocker) {
    if (Test-TcpPort -Port $dbPort -TimeoutMs 400) { Write-Info "端口 $dbPort ：已有服务监听（要么是本项目 MySQL，要么是别的库）" }
    else { Write-Info "端口 $dbPort ：空闲（MySQL 还没起来）" }
}

if ($CheckOnly) {
    Write-Title '体检结论'
    if ($Problems.Count -eq 0) {
        Write-Ok '一切就绪，直接跑 start-all.bat 即可拉起全部服务。'
        exit 0
    }
    Write-Warn2 ('待处理：' + ($Problems -join ', '))
    Write-Info '不加 -CheckOnly 再跑一次，脚本会尝试自动补齐（uv sync / npm install / 复制 .env）。'
    exit 1
}

# ================================================================
# 3. 自动补齐缺失依赖（只装依赖 / 复制配置，不碰代码）
# ================================================================
if ($Problems -contains 'env') {
    Write-Step '生成 .env'
    if (Test-Path -LiteralPath $EnvExample) {
        Copy-Item -LiteralPath $EnvExample -Destination $EnvFile -Force
        Write-Ok '已从 .env.example 复制出 .env'
        Write-Warn2 '如需 LLM 语义审查，请自行填 APP_LLM_ENABLED / APP_LLM_API_KEY'
    } else {
        Write-Err '既没有 .env 也没有 .env.example，后端可能起不来'
    }
}

if ($Problems -contains 'venv') {
    Write-Step '安装后端依赖（uv sync）'
    $uv = Get-ExeOnPath 'uv.exe'
    if (-not $uv) { $uv = Get-ExeOnPath 'uv' }
    if ($uv) {
        Push-Location $ProjectRoot
        & $uv sync
        Pop-Location
        if (Test-Path -LiteralPath $VenvPy) { Write-Ok '.venv 已就绪' }
        else { Write-Err 'uv sync 跑完但没看到 .venv\Scripts\python.exe，请手动确认' }
    } else {
        Write-Err '没找到 uv，无法自动装后端依赖。请手动执行：uv sync'
    }
}

if ($Problems -contains 'npm' -and $npmCmd) {
    Write-Step '安装前端依赖（npm install，第一次会慢一点）'
    Push-Location $FrontendDir
    & $npmCmd install
    Pop-Location
}

# ================================================================
# 4. Docker MySQL
# ================================================================
$dbReady = $false
if (-not $SkipDocker) {
    Write-Step '启动数据库（Docker MySQL）'

    if (Test-TcpPort -Port $dbPort -TimeoutMs 500) {
        Write-Ok "端口 $dbPort 已有服务在监听，跳过 Docker 启动"
        $dbReady = $true
    } elseif (-not $dockerExe) {
        Write-Err '没有 docker 命令，数据库起不来。请装/启动 Docker Desktop 后重试。'
    } else {
        # 守护进程没起来时，尝试拉起 Docker Desktop
        $daemonUp = $false
        try {
            & $dockerExe info 2>$null | Out-Null
            $daemonUp = ($LASTEXITCODE -eq 0)
        } catch { $daemonUp = $false }

        if (-not $daemonUp) {
            $desktopExe = Join-Path $env:ProgramFiles 'Docker\Docker\Docker Desktop.exe'
            if ((-not $NoDockerDesktop) -and (Test-Path -LiteralPath $desktopExe)) {
                Write-Warn2 'Docker 守护进程没起来，正在拉起 Docker Desktop…'
                try { Start-Process -FilePath $desktopExe | Out-Null } catch { }
                $waitDocker = 0
                while ($waitDocker -lt $TimeoutSeconds -and -not $daemonUp) {
                    Start-Sleep -Seconds 3
                    $waitDocker += 3
                    try { & $dockerExe info 2>$null | Out-Null; $daemonUp = ($LASTEXITCODE -eq 0) } catch { }
                }
            }
            if (-not $daemonUp) {
                Write-Err "Docker 守护进程不可用（等了 ${waitDocker}s）。请手动打开 Docker Desktop，等托盘图标变绿后重试。"
            }
        }

        if ($daemonUp) {
            Write-Ok 'Docker 守护进程可用'
            Push-Location $ProjectRoot
            & $dockerExe compose up -d mysql
            $composeCode = $LASTEXITCODE
            Pop-Location
            if ($composeCode -ne 0) {
                Write-Err "docker compose up -d mysql 失败（退出码 $composeCode）"
            } else {
                Write-Info '等待 MySQL 健康检查通过…'
                $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
                while ((Get-Date) -lt $deadline) {
                    $status = ''
                    try {
                        $status = (& $dockerExe inspect --format '{{.State.Health.Status}}' xm-contract-mysql 2>$null)
                    } catch { $status = '' }
                    if ($status -match 'healthy') { break }
                    Start-Sleep -Seconds 3
                }
                if ($status -match 'healthy') {
                    Write-Ok "MySQL 容器 xm-contract-mysql 健康（$dbPort -> 3306）"
                    $dbReady = $true
                } elseif (Test-TcpPort -Port $dbPort -TimeoutMs 500) {
                    Write-Warn2 'MySQL 还没报 healthy，但端口已通，先往下走'
                    $dbReady = $true
                } else {
                    Write-Err "MySQL 没起来（health=$status）。看日志：docker compose logs -f mysql"
                }
            }
        }
    }
} else {
    Write-Step '跳过数据库（-SkipDocker）'
    if (Test-TcpPort -Port $dbPort -TimeoutMs 500) { Write-Ok "端口 $dbPort 有服务在监听" }
    else { Write-Warn2 "端口 $dbPort 没人监听 —— 后端连接库会失败，确认 .env 的 APP_DB_URL 是否指向你那套 MySQL" }
}

# ================================================================
# 5. 后端
# ================================================================
$backendStarted = $false
if (-not $SkipBackend) {
    Write-Step '启动后端（FastAPI / uvicorn）'

    if (Test-TcpPort -Port $appPort -TimeoutMs 500) {
        Write-Ok "端口 $appPort 已被监听，判定后端已在运行，跳过启动"
        $backendStarted = $true
    } elseif (-not (Test-Path -LiteralPath $VenvPy)) {
        Write-Err "找不到 $VenvPy，无法启动后端。先跑：uv sync"
    } else {
        $cmd = "chcp 65001 > `$null; " +
               "try { [Console]::OutputEncoding=[System.Text.Encoding]::UTF8 } catch {}; " +
               "`$env:PYTHONIOENCODING='utf-8'; " +
               "`$Host.UI.RawUI.WindowTitle='XM-BACKEND :$appPort'; " +
               "Set-Location -LiteralPath '$ProjectRoot'; " +
               "& '$VenvPy' -m uvicorn app.main:app --host $appHost --port $appPort --reload"
        try {
            Start-Process -FilePath $ShellExe -ArgumentList @('-NoExit', '-NoLogo', '-NoProfile', '-Command', $cmd) `
                          -WorkingDirectory $ProjectRoot | Out-Null
            Write-Info '已在新窗口启动 uvicorn，等待端口就绪…'
            if (Wait-TcpPort -Port $appPort -Seconds $svcTimeout) {
                Write-Ok "后端已监听 $apiBase （接口文档 $apiBase/docs）"
                $backendStarted = $true
            } else {
                Write-Err "等了 $svcTimeout 秒后端仍没监听端口，去那个 'XM-BACKEND' 窗口看报错"
            }
        } catch {
            Write-Err "启动后端失败：$($_.Exception.Message)"
        }
    }
}

# ================================================================
# 6. 前端
# ================================================================
$webPort = 0
if (-not $SkipFrontend) {
    Write-Step '启动前端（Vite）'

    $existing = Get-ListeningPort -Ports $frontCandidates
    if ($existing -gt 0) {
        Write-Ok "端口 $existing 已被监听，判定前端已在运行，跳过启动"
        $webPort = $existing
    } elseif ($nodeExe -or $npmCmd) {
        # 优先直接用 node 跑 vite（等价于 npm run dev，但绕开 npm.cmd / .bin shim，少一层出问题的机会）
        $viteJs = Join-Path $FrontendDir 'node_modules\vite\bin\vite.js'
        if ($nodeExe -and (Test-Path -LiteralPath $viteJs)) {
            $launch = "& '$nodeExe' '$viteJs'"
            Write-Info "用 node 直接启动 vite（$viteJs）"
        } elseif ($npmCmd) {
            $launch = "& '$npmCmd' run dev"
            Write-Info "用 npm 启动 vite（$npmCmd）"
        } else {
            $launch = ''
        }

        if (-not $launch) {
            Write-Err '既没有 node+vite 也没有 npm，前端起不来'
        } else {
            $cmd = "chcp 65001 > `$null; " +
                   "try { [Console]::OutputEncoding=[System.Text.Encoding]::UTF8 } catch {}; " +
                   "`$Host.UI.RawUI.WindowTitle='XM-FRONTEND'; " +
                   "Set-Location -LiteralPath '$FrontendDir'; " +
                   $launch
            try {
                Start-Process -FilePath $ShellExe -ArgumentList @('-NoExit', '-NoLogo', '-NoProfile', '-Command', $cmd) `
                              -WorkingDirectory $FrontendDir | Out-Null
                Write-Info '已在新窗口启动 vite，等待端口就绪…'
                $deadline = (Get-Date).AddSeconds($svcTimeout)
                while ((Get-Date) -lt $deadline -and $webPort -eq 0) {
                    $webPort = Get-ListeningPort -Ports $frontCandidates
                    if ($webPort -eq 0) { Start-Sleep -Milliseconds 800 }
                }
                if ($webPort -gt 0) {
                    Write-Ok "前端已监听 http://localhost:$webPort"
                } else {
                    Write-Err "等了 $svcTimeout 秒前端仍没监听端口，去那个 'XM-FRONTEND' 窗口看报错"
                }
            } catch {
                Write-Err "启动前端失败：$($_.Exception.Message)"
            }
        }
    } else {
        Write-Err '没有 npm / node，前端起不来'
    }
}

# ================================================================
# 7. 汇总
# ================================================================
Write-Title '启动结果'
if (-not $SkipDocker) {
    if ($dbReady) { Write-Host ("   {0,-8}{1}" -f '数据库', "MySQL 8  →  127.0.0.1:$dbPort   容器 xm-contract-mysql") -ForegroundColor Green }
    else          { Write-Host ("   {0,-8}{1}" -f '数据库', '未就绪（见上面的报错）') -ForegroundColor Red }
}
if (-not $SkipBackend) {
    if ($backendStarted) { Write-Host ("   {0,-8}{1}" -f '后端', "FastAPI  →  $apiBase/docs") -ForegroundColor Green }
    else                 { Write-Host ("   {0,-8}{1}" -f '后端', '未就绪（见上面的报错）') -ForegroundColor Red }
}
if (-not $SkipFrontend) {
    if ($webPort -gt 0) {
        $entry = "http://localhost:$webPort"
        Write-Host ("   {0,-8}{1}" -f '前端', "Vite     →  $entry") -ForegroundColor Green
        Write-Host ("   {0,-8}{1}" -f '入口页', "$entry/login") -ForegroundColor Green
    } else {
        Write-Host ("   {0,-8}{1}" -f '前端', '未就绪（见上面的报错）') -ForegroundColor Red
    }
}
Write-Host ''
Write-Host '   演示账号：li.jing / lawyer123（法务）   admin / admin123（管理员）   zhang.wei / operator123（业务）' -ForegroundColor Gray
Write-Host '   关服务　：双击 stop-all.bat（会自动关掉上面的窗口进程，MySQL 数据保留）' -ForegroundColor Gray
Write-Host '   日志　　：storage\logs\app_时间.log         体检：start-all.bat -CheckOnly' -ForegroundColor Gray
Write-Host ('=' * 66) -ForegroundColor DarkCyan

if ((-not $NoBrowser) -and $webPort -gt 0) {
    try {
        Start-Process "http://localhost:$webPort/login" | Out-Null
        Write-Host ''
        Write-Host '   已打开浏览器。' -ForegroundColor Gray
    } catch { }
}

Write-Host ''
