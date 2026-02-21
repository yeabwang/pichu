@echo off
setlocal EnableExtensions

set "SCRIPT_DIR=%~dp0"
set "LOCAL_SCRIPT=%SCRIPT_DIR%install.ps1"

if exist "%LOCAL_SCRIPT%" (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%LOCAL_SCRIPT%" %*
    exit /b %errorlevel%
)

rem Allow overriding the branch used to download install.ps1 (default: main)
if "%PICHU_INSTALL_BRANCH%"=="" set "PICHU_INSTALL_BRANCH=main"

set "REMOTE_SCRIPT=%TEMP%\pichu-install-%RANDOM%%RANDOM%%RANDOM%.ps1"
curl -fsSL "https://raw.githubusercontent.com/yeabwang/pichu/%PICHU_INSTALL_BRANCH%/install.ps1" -o "%REMOTE_SCRIPT%"
if errorlevel 1 (
    echo Failed to download install.ps1 from GitHub.
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -Command "& { param([string]$scriptPath, [string[]]$scriptArgs) try { & $scriptPath @scriptArgs; exit $LASTEXITCODE } finally { Remove-Item -LiteralPath $scriptPath -ErrorAction SilentlyContinue } }" "%REMOTE_SCRIPT%" %*
set "EXIT_CODE=%errorlevel%"
exit /b %EXIT_CODE%
