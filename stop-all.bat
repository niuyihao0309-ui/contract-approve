@echo off
chcp 65001 >nul
setlocal
set "PS=powershell"
where pwsh >nul 2>nul && set "PS=pwsh"
"%PS%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\stop_all.ps1" %*
echo.
echo [press any key to close this window]
pause >nul
endlocal
