@echo off
setlocal enableextensions
title XM Contract Approve - Deploy to Server
cd /d "%~dp0"

REM ===========================================================================
REM  One-click deploy: local  ->  cloud server (82.157.59.218)
REM    1. build frontend        npm run build
REM    2. upload frontend       -> /var/www/contract   (tar + scp + atomic swap)
REM    3. sync backend code     -> /opt/contract       (tar, excludes junk)
REM    4. restart backend service
REM
REM  IMPORTANT: keep this file PURE ASCII with CRLF line endings.
REM  Non-ASCII bytes together with "chcp 65001" make cmd.exe mis-read the file
REM  and execute fragments of comments as commands.
REM
REM  NOTE: the server login is the unprivileged "ubuntu" user, so privileged
REM  steps carry an explicit "sudo -n" (passwordless sudo is enabled).
REM ===========================================================================

set "SRV=ubuntu@82.157.59.218"
set "KEY=%USERPROFILE%\.ssh\itnyh.pem"
set "NPM=C:\Program Files\nodejs\npm.cmd"

REM --- Use the Windows built-ins by ABSOLUTE path. -------------------------
REM If a Git-for-Windows install sits earlier on PATH it shadows these with
REM MSYS builds, which treat a backslash as an escape character: the path
REM "dist\*" silently turns into "dist*" and scp then fails with
REM 'stat local "...dist*": No such file or directory'.
set "TAR=%SystemRoot%\System32\tar.exe"
set "SSHEXE=%SystemRoot%\System32\OpenSSH\ssh.exe"
set "SCPEXE=%SystemRoot%\System32\OpenSSH\scp.exe"
if not exist "%TAR%"    set "TAR=tar"
if not exist "%SSHEXE%" set "SSHEXE=ssh"
if not exist "%SCPEXE%" set "SCPEXE=scp"

REM Forward-slash copies of Windows paths, safe for both implementations.
set "KEYU=%KEY:\=/%"
set "TMPU=%TEMP:\=/%"
set "SSHOPT=-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=20"

if not exist "%KEY%" (
    echo [ERROR] SSH key not found: %KEY%
    pause
    exit /b 1
)

echo.
echo ============================================================
echo        Deploy  Contract  -^>  %SRV%
echo ------------------------------------------------------------
echo   1  frontend  build + upload  -^>  /var/www/contract
echo   2  backend   code sync       -^>  /opt/contract
echo   3  restart   backend service
echo ============================================================
echo.

REM ---------------------------------------------------------------- 1/4
echo [1/4] Building frontend ...
pushd frontend
call "%NPM%" run build
if errorlevel 1 (
    echo.
    echo [ERROR] Frontend build failed. Nothing was uploaded.
    popd
    pause
    exit /b 1
)
popd
echo       build ok
echo.

REM ---------------------------------------------------------------- 2/4
echo [2/4] Uploading frontend ...
"%TAR%" -czf "%TEMP%\contract-dist.tgz" -C frontend dist
if errorlevel 1 ( echo [ERROR] tar failed. & pause & exit /b 1 )
"%SCPEXE%" -i "%KEYU%" %SSHOPT% "%TMPU%/contract-dist.tgz" %SRV%:/tmp/
if errorlevel 1 ( echo [ERROR] scp failed. & pause & exit /b 1 )
"%SSHEXE%" -i "%KEYU%" %SSHOPT% %SRV% "sudo -n rm -rf /var/www/contract.new && sudo -n mkdir -p /var/www/contract.new && sudo -n tar xzf /tmp/contract-dist.tgz -C /var/www/contract.new --strip-components=1 && sudo -n rm -rf /var/www/contract.old && sudo -n mv /var/www/contract /var/www/contract.old && sudo -n mv /var/www/contract.new /var/www/contract && sudo -n chown -R caddy:caddy /var/www/contract && rm -f /tmp/contract-dist.tgz"
if errorlevel 1 ( echo [ERROR] remote step failed. & pause & exit /b 1 )
echo       upload ok
echo.

REM ---------------------------------------------------------------- 3/4
echo [3/4] Syncing backend code ...
REM .env is DELIBERATELY excluded: the server copy holds the production
REM DB url / auth secret / LLM key and must not be overwritten by the
REM local development .env. frontend/ goes up as dist only (step 2).
"%TAR%" -czf "%TEMP%\contract-src.tgz" ^
    --exclude=.venv ^
    --exclude=.git ^
    --exclude=.idea ^
    --exclude=.pytest_cache ^
    --exclude=.workbuddy ^
    --exclude=.env ^
    --exclude=storage ^
    --exclude=frontend ^
    --exclude=node_modules ^
    --exclude=dist ^
    --exclude=__pycache__ ^
    .
if errorlevel 1 ( echo [ERROR] tar failed. & pause & exit /b 1 )
"%SCPEXE%" -i "%KEYU%" %SSHOPT% "%TMPU%/contract-src.tgz" %SRV%:/tmp/
if errorlevel 1 ( echo [ERROR] scp failed. & pause & exit /b 1 )
"%SSHEXE%" -i "%KEYU%" %SSHOPT% %SRV% "tar xzf /tmp/contract-src.tgz -C /opt/contract && rm -f /tmp/contract-src.tgz"
if errorlevel 1 ( echo [ERROR] remote extract failed. & pause & exit /b 1 )
echo       sync ok
echo.

REM ---------------------------------------------------------------- 4/4
echo [4/4] Restarting backend service ...
"%SSHEXE%" -i "%KEYU%" %SSHOPT% %SRV% "sudo -n systemctl restart contract-backend && sleep 6 && systemctl is-active contract-backend"
if errorlevel 1 ( echo [WARN] restart reported an error - check server logs. )

del /q "%TEMP%\contract-dist.tgz" >nul 2>nul
del /q "%TEMP%\contract-src.tgz"  >nul 2>nul

echo.
echo ============================================================
echo   Done.   http://82.157.59.218:8080/
echo ------------------------------------------------------------
echo   Backend log : ssh -i "%KEYU%" %SRV% "sudo -n journalctl -u contract-backend -n 50"
echo   Admin panel : http://82.157.59.218/__gate/admin?token=SEE_CLIENT
echo   Gate state  : http://82.157.59.218:8080/__gate/status
echo ============================================================
echo.
pause
