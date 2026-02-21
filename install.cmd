@echo off
setlocal EnableExtensions DisableDelayedExpansion

chcp 65001 > nul

set "LOCAL_SCRIPT=%~dp0install.ps1"
if exist "%LOCAL_SCRIPT%" (
    powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass ^
        -File "%LOCAL_SCRIPT%" %*
    exit /b %errorlevel%
)

for /f "usebackq delims=" %%T in (
    `powershell.exe -NoProfile -NonInteractive -Command "[System.IO.Path]::GetTempFileName()"`
) do set "TEMP_BASE=%%T"

if not defined TEMP_BASE (
    echo error: Failed to allocate a temporary file. >&2
    exit /b 1
)

set "REMOTE_SCRIPT=%TEMP_BASE%.ps1"
rename "%TEMP_BASE%" "%~nx0-%TEMP_BASE:~-8%.ps1" > nul 2>&1
if not exist "%REMOTE_SCRIPT%" set "REMOTE_SCRIPT=%TEMP_BASE%"

curl.exe --proto "=https" --tlsv1.2 -fsSL --max-filesize 1048576 ^
    "https://raw.githubusercontent.com/yeabwang/pichu/main/install.ps1" ^
    -o "%REMOTE_SCRIPT%"

if errorlevel 1 (
    echo error: Failed to download install.ps1. >&2
    del /f /q "%REMOTE_SCRIPT%" > nul 2>&1
    del /f /q "%TEMP_BASE%"     > nul 2>&1
    exit /b 1
)

powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass ^
    -File "%REMOTE_SCRIPT%" %*

set "PS_EXIT=%errorlevel%"

del /f /q "%REMOTE_SCRIPT%" > nul 2>&1
del /f /q "%TEMP_BASE%"     > nul 2>&1

exit /b %PS_EXIT%