@echo off
::
:: pichu installer bootstrap for Windows (CMD).
:: Delegates to install.ps1, either from a local copy or downloaded from GitHub.
::
:: Configure via environment variables before running this script:
::   PICHU_ALIAS, PICHU_NO_MODIFY_PATH, PICHU_INSTALL_DIR, PICHU_VERSION
:: Or invoke install.ps1 directly from PowerShell to pass parameters.
::
setlocal EnableExtensions DisableDelayedExpansion

:: ── UTF-8 code page ─────────────────────────────────────────────────────────
chcp 65001 > nul

:: ── Prefer a local copy of install.ps1 ──────────────────────────────────────
set "LOCAL_SCRIPT=%~dp0install.ps1"
if exist "%LOCAL_SCRIPT%" (
    powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass ^
        -File "%LOCAL_SCRIPT%"
    exit /b %errorlevel%
)

:: ── Download to a temporary file ──────────────────────────────────────────────
for /f "usebackq delims=" %%T in (
    `powershell.exe -NoProfile -NonInteractive -Command "[System.IO.Path]::GetTempFileName()"`
) do set "TEMP_BASE=%%T"

if not defined TEMP_BASE (
    echo error: Failed to allocate a temporary file. >&2
    exit /b 1
)

del /f /q "%TEMP_BASE%" >nul 2>&1
set "REMOTE_SCRIPT=%TEMP_BASE%.ps1"

:: ── Download ─────────────────────────────────────────────────────────────────
curl.exe --proto "=https" --tlsv1.2 -fsSL --max-filesize 1048576 ^
    "https://raw.githubusercontent.com/yeabwang/pichu/main/install.ps1" ^
    -o "%REMOTE_SCRIPT%"

if errorlevel 1 (
    echo error: Failed to download install.ps1. >&2
    del /f /q "%REMOTE_SCRIPT%" > nul 2>&1
    exit /b 1
)

:: ── Verify downloaded file is non-empty ────────────────────────────────────────
for %%A in ("%REMOTE_SCRIPT%") do if %%~zA EQU 0 (
    echo error: Downloaded install.ps1 is empty. >&2
    del /f /q "%REMOTE_SCRIPT%" > nul 2>&1
    exit /b 1
)

:: ── Execute ───────────────────────────────────────────────────────────────────
powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass ^
    -File "%REMOTE_SCRIPT%"

set "PS_EXIT=%errorlevel%"

:: ── Cleanup ───────────────────────────────────────────────────────────────────
del /f /q "%REMOTE_SCRIPT%" > nul 2>&1

exit /b %PS_EXIT%
