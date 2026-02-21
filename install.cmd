@echo off
setlocal EnableExtensions

set "SCRIPT_DIR=%~dp0"
set "LOCAL_SCRIPT=%SCRIPT_DIR%install.ps1"

if exist "%LOCAL_SCRIPT%" (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%LOCAL_SCRIPT%" %*
    exit /b %errorlevel%
)

set "REMOTE_SCRIPT=%TEMP%\pichu-install-%RANDOM%%RANDOM%.ps1"
curl -fsSL "https://raw.githubusercontent.com/yeabwang/pichu/main/install.ps1" -o "%REMOTE_SCRIPT%"
if errorlevel 1 (
    echo Failed to download install.ps1 from GitHub.
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%REMOTE_SCRIPT%" %*
set "EXIT_CODE=%errorlevel%"
del /q "%REMOTE_SCRIPT%" >nul 2>&1
exit /b %EXIT_CODE%
