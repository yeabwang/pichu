#Requires -Version 5.1
<#
.SYNOPSIS
    pichu installer for Windows (PowerShell).

.DESCRIPTION
    Installs pichu using uv, pipx, pip3, or pip.
    Optionally adds the install directory to the user PATH and configures
    a PowerShell alias in the user's profile.

.PARAMETER Alias
    Override the alias name (default: pichu).

.PARAMETER NoModifyPath
    Do not modify the user PATH environment variable.

.PARAMETER InstallDir
    Override the install/bin directory hint.

.PARAMETER Version
    Git ref (tag, branch, or commit) to install (default: main).

.PARAMETER Help
    Show usage information.

.EXAMPLE
    .\install.ps1
#>

[CmdletBinding()]
param(
    [string] $Alias      = $(if ($env:PICHU_ALIAS)       { $env:PICHU_ALIAS }       else { 'pichu' }),
    [switch] $NoModifyPath,
    [string] $InstallDir = $(if ($env:PICHU_INSTALL_DIR) { $env:PICHU_INSTALL_DIR } else { "$env:USERPROFILE\.local\bin" }),
    [string] $Version    = $(if ($env:PICHU_VERSION)     { $env:PICHU_VERSION }     else { 'main' }),
    [switch] $Help
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ConfirmPreference = 'None'

# ── Enforce TLS 1.2+ ─────────────────────────────────────────────────────────
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

# ── Honour env override for NoModifyPath ─────────────────────────────────────
if ($env:PICHU_NO_MODIFY_PATH -eq '1') { $NoModifyPath = $true }

# ── Module-level state ────────────────────────────────────────────────────────
$repo           = 'yeabwang/pichu'
$pathTargetDir  = $InstallDir  # may be updated after install locates the binary
$profileFile    = $null

$pathModified      = $false
$pathAlreadyPresent = $false
$pathSkipped        = $false
$pathError          = $false

$aliasAdded         = $false
$aliasAlreadyPresent = $false
$aliasConflict      = $false
$aliasError         = $false

# ── Helpers ───────────────────────────────────────────────────────────────────
function Write-Info([string]$Message) { Write-Host "==> $Message" -ForegroundColor Cyan }
function Write-Warn([string]$Message) { Write-Warning $Message }
function Fail([string]$Message)       { throw $Message }

function Show-Usage {
    @(
        'pichu installer',
        '',
        'Options:',
        '  -Alias <name>       Override alias name (default: pichu)',
        '  -NoModifyPath       Do not modify user PATH',
        '  -InstallDir <path>  Override install/bin directory hint',
        '  -Version <ref>      Git ref to install (default: main)',
        '  -Help               Show this help',
        '',
        'Environment:',
        '  PICHU_INSTALL_DIR       Override install/bin directory hint',
        '  PICHU_ALIAS             Alias name (default: pichu)',
        '  PICHU_NO_MODIFY_PATH=1  Skip PATH modification',
        '  PICHU_VERSION           Git ref to install (default: main)',
        '',
        'Security note:',
        '  Download to disk before executing — never pipe remote scripts directly',
        '  into PowerShell. Example:',
        '    $tmp = New-TemporaryFile',
        '    Invoke-WebRequest -Uri <url> -OutFile $tmp.FullName -UseBasicParsing',
        '    & $tmp.FullName',
        '    Remove-Item $tmp.FullName -Force'
    ) | ForEach-Object { Write-Output $_ }
}

# ── Alias validation ──────────────────────────────────────────────────────────
function Test-AliasName([string]$AliasName) {
    if ([string]::IsNullOrWhiteSpace($AliasName)) { return }

    if ($AliasName.StartsWith('-')) {
        Fail "Invalid alias '$AliasName': must not start with '-'."
    }

    # 1–32 chars, starts with letter, ends with letter/digit,
    # interior may include letters, digits, underscores, or hyphens.
    if ($AliasName -notmatch '^[A-Za-z](?:[A-Za-z0-9_-]{0,30}[A-Za-z0-9_])?$') {
        Fail "Invalid alias '$AliasName'. Use 1–32 chars: letters, numbers, _ or -, starting with a letter and not ending with '-'."
    }

    $reserved = @(
        'alias','break','catch','class','continue','data','do','dynamicparam',
        'else','elseif','end','exit','filter','finally','for','foreach','from',
        'function','hidden','if','in','param','process','return','switch',
        'throw','trap','try','until','using','var','while'
    )
    if ($reserved -contains $AliasName.ToLowerInvariant()) {
        Fail "Alias '$AliasName' is reserved by PowerShell."
    }
}

# ── Binary location ───────────────────────────────────────────────────────────
function Get-CommandPath([string]$Name) {
    $cmd = Get-Command $Name -ErrorAction SilentlyContinue
    if ($null -eq $cmd) { return $null }

    foreach ($prop in @('Source','Path','Definition')) {
        $propObj = $cmd.PSObject.Properties[$prop]
        $val = if ($propObj) { $propObj.Value } else { $null }
        if ($val -and (Test-Path -LiteralPath $val -PathType Leaf)) {
            return $val
        }
    }
    return $null
}

# ── Python version check ───────────────────────────────────────────────────────
function Get-PythonVersion {
    foreach ($exe in @('python','py')) {
        $cmd = Get-Command $exe -ErrorAction SilentlyContinue
        if (-not $cmd) { continue }
        $pyArgs = if ($exe -eq 'py') { @('-3', '-c') } else { @('-c') }
        $ver = & $exe @pyArgs "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
        if ($ver) { return $ver.Trim() }
    }
    return $null
}

# ── PATH helpers ───────────────────────────────────────────────────────────────
function Get-NormalisedSegment([string]$Seg) {
    $Seg.Trim().TrimEnd('\').ToLowerInvariant()
}

function Split-PathSegments([string]$PathValue) {
    if ([string]::IsNullOrWhiteSpace($PathValue)) { return @() }
    return $PathValue -split ';' | ForEach-Object { $_.Trim() } | Where-Object { $_ }
}

function Test-PathContains([string]$PathValue, [string]$Candidate) {
    $target = Get-NormalisedSegment $Candidate
    foreach ($seg in (Split-PathSegments $PathValue)) {
        if ((Get-NormalisedSegment $seg) -eq $target) { return $true }
    }
    return $false
}

function Set-PathEntry([string]$Dir, [bool]$Skip) {
    if ($Skip) { $script:pathSkipped = $true; return }

    $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    if (Test-PathContains $userPath $Dir) { $script:pathAlreadyPresent = $true; return }

    try {
        $newPath = if ([string]::IsNullOrWhiteSpace($userPath)) { $Dir } else { "$Dir;$userPath" }
        [Environment]::SetEnvironmentVariable('Path', $newPath, 'User')
        # Only update the process PATH after the registry write succeeded.
        $env:Path = "$Dir;$env:Path"
        $script:pathModified = $true
    }
    catch {
        $script:pathError = $true
        Write-Warn "Failed to update user PATH: $($_.Exception.Message)"
    }
}

# ── Profile-file safety ────────────────────────────────────────────────────────
function Assert-ProfileFileSafe([string]$ProfilePath) {
    $parentDir = Split-Path -Parent $ProfilePath

    if (-not (Test-Path -LiteralPath $parentDir)) {
        New-Item -ItemType Directory -LiteralPath $parentDir -Force | Out-Null

        try {
            $acl = Get-Acl -LiteralPath $parentDir
            $acl.SetAccessRuleProtection($true, $false)
            $rule = [System.Security.AccessControl.FileSystemAccessRule]::new(
                [System.Security.Principal.WindowsIdentity]::GetCurrent().Name,
                'FullControl',
                'ContainerInherit,ObjectInherit',
                'None',
                'Allow'
            )
            $acl.AddAccessRule($rule)
            Set-Acl -LiteralPath $parentDir -AclObject $acl -ErrorAction SilentlyContinue
        } catch {
            # Non-fatal; warn and continue.
            Write-Warn "Could not harden directory permissions on $parentDir"
        }
    }

    if (-not (Test-Path -LiteralPath $ProfilePath)) {
        New-Item -ItemType File -LiteralPath $ProfilePath -Force | Out-Null
        return
    }

    $fileInfo = Get-Item -LiteralPath $ProfilePath -Force -ErrorAction SilentlyContinue
    if ($fileInfo -and ($fileInfo.Attributes -band [System.IO.FileAttributes]::ReparsePoint)) {
        Fail "Profile file '$ProfilePath' is a symbolic link or junction — refusing to write."
    }
}

# ── Alias entry management ────────────────────────────────────────────────────
function Set-AliasEntry([string]$AliasName) {
    if ([string]::IsNullOrWhiteSpace($AliasName)) { return }

    $existingAlias = Get-Alias -Name $AliasName -ErrorAction SilentlyContinue
    if ($existingAlias) {
        if ($existingAlias.Definition -ieq 'pichu') {
            $script:aliasAlreadyPresent = $true; return
        }
        $script:aliasConflict = $true; return
    }

    $script:profileFile = $PROFILE.CurrentUserAllHosts

    try { Assert-ProfileFileSafe $script:profileFile }
    catch {
        $script:aliasError = $true
        Write-Warn "Cannot write profile '$script:profileFile': $($_.Exception.Message)"
        return
    }

    # Read the file with an explicit encoding to avoid BOM/codepage issues.
    $content = Get-Content -LiteralPath $script:profileFile -Raw -Encoding UTF8 -ErrorAction SilentlyContinue
    if ($null -eq $content) { $content = '' }

    $escaped = [Regex]::Escape($AliasName)

    $exactPatterns = @(
        "(?im)^\s*Set-Alias\s+-Name\s+$escaped\s+-Value\s+pichu(?:\s|$)",
        "(?im)^\s*Set-Alias\s+$escaped\s+pichu(?:\s|$)",
        "(?im)^\s*New-Alias\s+-Name\s+$escaped\s+-Value\s+pichu(?:\s|$)"
    )
    foreach ($p in $exactPatterns) {
        if ($content -match $p) {
            $script:aliasAlreadyPresent = $true
            Set-Alias -Name $AliasName -Value pichu -ErrorAction SilentlyContinue
            return
        }
    }

    $conflictPatterns = @(
        "(?im)^\s*Set-Alias\s+-Name\s+$escaped\b",
        "(?im)^\s*Set-Alias\s+$escaped\b",
        "(?im)^\s*New-Alias\s+-Name\s+$escaped\b",
        "(?im)^\s*function\s+$escaped\b"
    )
    foreach ($p in $conflictPatterns) {
        if ($content -match $p) { $script:aliasConflict = $true; return }
    }

    # Append the alias block with explicit UTF-8 encoding (no BOM).
    $safeAlias = $AliasName -replace "'", "''"
    $block = @"

# >>> pichu alias >>>
Set-Alias -Name '$safeAlias' -Value pichu
# <<< pichu alias <<<

"@

    $fi = Get-Item -LiteralPath $script:profileFile -Force -ErrorAction SilentlyContinue
    if ($fi -and ($fi.Attributes -band [System.IO.FileAttributes]::ReparsePoint)) {
        $script:aliasError = $true
        Write-Warn "Profile '$script:profileFile' is a symlink — refusing to write."
        return
    }
    Add-Content -LiteralPath $script:profileFile -Value $block -Encoding UTF8 -NoNewline:$false

    Set-Alias -Name $AliasName -Value pichu -ErrorAction SilentlyContinue
    $script:aliasAdded = $true
}

# ── Package installation ───────────────────────────────────────────────────────
function Select-Installer {
    foreach ($tool in @('uv','pipx','pip3','pip')) {
        if (Get-Command $tool -ErrorAction SilentlyContinue) { return $tool }
    }
    Fail 'No package installer found. Install uv from https://docs.astral.sh/uv/getting-started/installation/'
}

function Install-Pichu([string]$Installer) {
    Write-Info "Installing pichu with $Installer…"
    $gitUrl = "git+https://github.com/$repo.git@$Version"
    $pkgRef = "pichu @ $gitUrl"

    switch ($Installer) {
        'uv'   { & uv tool install --force "$pkgRef" }
        'pipx' { & pipx install "$gitUrl" }
        'pip3' { & pip3 install --user "$gitUrl" }
        'pip'  { & pip  install --user "$gitUrl" }
        default { Fail "Unsupported installer '$Installer'." }
    }

    if ($LASTEXITCODE -ne 0) { Fail "Failed to install pichu with $Installer (exit $LASTEXITCODE)." }
}

# ── Summary ────────────────────────────────────────────────────────────────────
function Write-Summary {
    Write-Output ''

    if      ($pathModified)       { Write-Output "PATH updated — restart your shell to pick up the change." }
    elseif  ($pathAlreadyPresent) { Write-Output "PATH already contains the install directory." }
    elseif  ($pathSkipped)        { Write-Output "PATH modification skipped (-NoModifyPath)." }
    else                          { Write-Output "PATH was not modified automatically." }

    if (-not [string]::IsNullOrWhiteSpace($Alias)) {
        if      ($aliasAdded)          { Write-Output "Alias '$Alias' added to $script:profileFile." }
        elseif  ($aliasAlreadyPresent) { Write-Output "Alias '$Alias' already configured." }
        elseif  ($aliasConflict)       { Write-Output "Alias '$Alias' already exists with a different definition — not modified." }
        else                           { Write-Output "Alias '$Alias' was not configured automatically." }
    }

    if ((Get-CommandPath 'pichu') -or $pathModified) {
        Write-Output "Successfully installed. Run 'pichu' from anywhere."
    } else {
        Write-Output "Installed, but 'pichu' is not yet on PATH in this shell."
        Write-Output "Manual PATH command:"
        Write-Output "  `$env:Path = `"$pathTargetDir;`$env:Path`""
        Write-Output "User PATH target:"
        Write-Output "  $pathTargetDir"
    }
}

# ════════════════════════════════════════════════════════════════════════════
if ($Help) { Show-Usage; exit 0 }

Test-AliasName $Alias

# Validate InstallDir is a rooted (absolute) path
if (-not [System.IO.Path]::IsPathRooted($InstallDir)) {
    Fail "InstallDir must be an absolute path: '$InstallDir'"
}

if ($Version -notmatch '^[A-Za-z0-9._/-]+$') {
    Fail "Version contains invalid characters: '$Version'"
}

$verText = Get-PythonVersion
if ([string]::IsNullOrWhiteSpace($verText)) {
    Fail 'Python 3.11+ is required. Install it first.'
}

if ($verText -notmatch '^\d+\.\d+$') {
    Fail "Unexpected Python version output: '$verText'"
}
$verParts = $verText.Split('.')

$major = [int]$verParts[0]
$minor = [int]$verParts[1]
if ($major -lt 3 -or ($major -eq 3 -and $minor -lt 11)) {
    Fail "Python 3.11+ required (found $verText)."
}

$installer = Select-Installer
Install-Pichu $installer

$expectedBin = Join-Path $InstallDir 'pichu'
$expectedExe = Join-Path $InstallDir 'pichu.exe'
if ((Test-Path -LiteralPath $expectedBin -PathType Leaf) -or
    (Test-Path -LiteralPath $expectedExe -PathType Leaf)) {
    $pathTargetDir = $InstallDir
} else {
    $pichuPath = Get-CommandPath 'pichu'
    if ($pichuPath) { $pathTargetDir = Split-Path -Parent $pichuPath }
}

Set-PathEntry -Dir $pathTargetDir -Skip:($NoModifyPath.IsPresent)
Set-AliasEntry $Alias
Write-Summary

if ($pathError -or $aliasError) { exit 1 }