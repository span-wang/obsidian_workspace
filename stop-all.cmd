@echo off
setlocal

set "powerShellExe=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
if not exist "%powerShellExe%" set "powerShellExe=%LOCALAPPDATA%\Microsoft\WindowsApps\pwsh.exe"
if not exist "%powerShellExe%" (
    echo PowerShell was not found. Install PowerShell or restore the Windows PowerShell executable.
    set "exitCode=1"
    goto :finish
)

"%powerShellExe%" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\stop-all.ps1"
set "exitCode=%ERRORLEVEL%"

:finish
echo.
if not "%exitCode%"=="0" echo Shutdown failed with exit code %exitCode%.
pause
endlocal & exit /b %exitCode%
