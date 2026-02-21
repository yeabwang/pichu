param(
    [string]$Alias = $(if ([string]::IsNullOrWhiteSpace($env:PICHU_ALIAS)) { "pichu" } else { $env:PICHU_ALIAS }),
    [switch]$NoModifyPath,
    [string]$InstallDir = $(if ($env:PICHU_INSTALL_DIR) { $env:PICHU_INSTALL_DIR } else { "$env:USERPROFILE\.local\bin" }),
    [switch]$Help
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ($env:PICHU_NO_MODIFY_PATH -eq "1") {
    $NoModifyPath = $true
}

$repo = "yeabwang/pichu"
$pathTargetDir = $InstallDir
$profileFile = $null

$pathModified = $false
$pathAlreadyPresent = $false
$pathSkipped = $false
$pathError = $false

$aliasAdded = $false
$aliasAlreadyPresent = $false
$aliasConflict = $false
$aliasError = $false

function Show-Usage {
    @(
        "pichu installer",
        "",
        "Options:",
        "  -Alias <name>       Override alias name (default: pichu)",
        "  -NoModifyPath       Do not modify user PATH",
        "  -InstallDir <path>  Override install/bin directory hint",
        "  -Help               Show this help",
        "",
        "Environment:",
        "  PICHU_INSTALL_DIR       Override install/bin directory hint",
        "  PICHU_ALIAS             Alias name (default: pichu)",
        "  PICHU_NO_MODIFY_PATH=1  Skip PATH modification"
    ) | ForEach-Object { Write-Output $_ }
}

function Write-Info([string]$Message) {
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Write-Warn([string]$Message) {
    Write-Warning $Message
}

function Fail([string]$Message) {
    throw $Message
}

function Test-AliasName([string]$AliasName) {
    if ([string]::IsNullOrWhiteSpace($AliasName)) {
        return
    }

    if ($AliasName -notmatch "^[A-Za-z](?:[A-Za-z0-9_-]{0,30}[A-Za-z0-9_])?$") {
        Fail "Invalid alias '$AliasName'. Use 1-32 chars: letters, numbers, _ or -, starting with a letter and not ending with '-'."
    }

    $reserved = @(
        "alias", "break", "catch", "class", "continue", "data", "do", "dynamicparam", "else", "elseif", "end",
        "exit", "filter", "finally", "for", "foreach", "from", "function", "hidden", "if", "in", "param",
        "process", "return", "switch", "throw", "trap", "try", "until", "using", "var", "while"
    )
    if ($reserved -contains $AliasName.ToLowerInvariant()) {
        Fail "Alias '$AliasName' is reserved by PowerShell."
    }
}

function Get-CommandPath([string]$Name) {
    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if ($null -eq $command) {
        return $null
    }
    if ($command.PSObject.Properties.Match("Source").Count -gt 0 -and $command.Source) {
        return $command.Source
    }
    if ($command.PSObject.Properties.Match("Path").Count -gt 0 -and $command.Path) {
        return $command.Path
    }
    if ($command.PSObject.Properties.Match("Definition").Count -gt 0 -and (Test-Path -LiteralPath $command.Definition -PathType Leaf)) {
        return $command.Definition
    }
    return $null
}

function Get-PythonVersion {
    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) {
        return (& python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
    }

    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) {
        return (& py -3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
    }

    return $null
}

function Refresh-ProcessPath {
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    $machinePath = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $env:Path = (@($userPath, $machinePath, $env:Path) | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }) -join ";"
}

function Select-Installer {
    if (Get-Command uv -ErrorAction SilentlyContinue) {
        return "uv"
    }
    if (Get-Command pipx -ErrorAction SilentlyContinue) {
        return "pipx"
    }
    if (Get-Command pip3 -ErrorAction SilentlyContinue) {
        return "pip3"
    }
    if (Get-Command pip -ErrorAction SilentlyContinue) {
        return "pip"
    }

    Fail "No package installer found. Install uv manually from https://docs.astral.sh/uv/getting-started/installation/."
}

function Install-Pichu([string]$Installer) {
    Write-Info "Installing pichu with $Installer..."
    switch ($Installer) {
        "uv" {
            & uv tool install --force "pichu @ git+https://github.com/$repo.git"
        }
        "pipx" {
            & pipx install "git+https://github.com/$repo.git"
        }
        "pip3" {
            & pip3 install --user "git+https://github.com/$repo.git"
        }
        "pip" {
            & pip install --user "git+https://github.com/$repo.git"
        }
        default {
            Fail "Unsupported installer '$Installer'."
        }
    }

    if ($LASTEXITCODE -ne 0) {
        Fail "Failed to install pichu with $Installer."
    }
}

function Normalize-PathSegment([string]$PathValue) {
    return $PathValue.Trim().TrimEnd("\").ToLowerInvariant()
}

function Split-PathSegments([string]$PathValue) {
    if ([string]::IsNullOrWhiteSpace($PathValue)) {
        return @()
    }
    return ($PathValue -split ";") | ForEach-Object { $_.Trim() } | Where-Object { $_ }
}

function Test-PathContains([string]$PathValue, [string]$Candidate) {
    $target = Normalize-PathSegment $Candidate
    foreach ($segment in (Split-PathSegments $PathValue)) {
        if ((Normalize-PathSegment $segment) -eq $target) {
            return $true
        }
    }
    return $false
}

function Ensure-PathEntry([string]$Dir, [bool]$Skip) {
    if ($Skip) {
        $script:pathSkipped = $true
        return
    }

    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if (Test-PathContains $userPath $Dir) {
        $script:pathAlreadyPresent = $true
        return
    }

    try {
        $newPath = if ([string]::IsNullOrWhiteSpace($userPath)) { $Dir } else { "$Dir;$userPath" }
        [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
        $env:Path = "$Dir;$env:Path"
        $script:pathModified = $true
    }
    catch {
        $script:pathError = $true
        Write-Warn "Failed to update user PATH automatically: $($_.Exception.Message)"
    }
}

function Ensure-ProfileFile([string]$ProfilePath) {
    $profileDirectory = Split-Path -Parent $ProfilePath
    if (-not (Test-Path -LiteralPath $profileDirectory)) {
        New-Item -ItemType Directory -Path $profileDirectory -Force | Out-Null
    }
    if (-not (Test-Path -LiteralPath $ProfilePath)) {
        New-Item -ItemType File -Path $ProfilePath -Force | Out-Null
    }
}

function Ensure-AliasEntry([string]$AliasName) {
    if ([string]::IsNullOrWhiteSpace($AliasName)) {
        return
    }

    $existingAlias = Get-Alias -Name $AliasName -ErrorAction SilentlyContinue
    if ($existingAlias) {
        if ($existingAlias.Definition -eq "pichu") {
            $script:aliasAlreadyPresent = $true
            return
        }
        $script:aliasConflict = $true
        return
    }

    $script:profileFile = $PROFILE.CurrentUserAllHosts
    try {
        Ensure-ProfileFile $script:profileFile
    }
    catch {
        $script:aliasError = $true
        Write-Warn "Failed to prepare profile file '$script:profileFile': $($_.Exception.Message)"
        return
    }

    $content = Get-Content -Path $script:profileFile -Raw
    $escaped = [Regex]::Escape($AliasName)
    $exactPatterns = @(
        "(?im)^\s*Set-Alias\s+-Name\s+$escaped\s+-Value\s+pichu(?:\s|$)",
        "(?im)^\s*Set-Alias\s+$escaped\s+pichu(?:\s|$)",
        "(?im)^\s*New-Alias\s+-Name\s+$escaped\s+-Value\s+pichu(?:\s|$)"
    )

    foreach ($pattern in $exactPatterns) {
        if ($content -match $pattern) {
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

    foreach ($pattern in $conflictPatterns) {
        if ($content -match $pattern) {
            $script:aliasConflict = $true
            return
        }
    }

    $block = @'
# >>> pichu alias >>>
Set-Alias -Name {0} -Value pichu
# <<< pichu alias <<<
'@ -f $AliasName
    Add-Content -Path $script:profileFile -Value "`n$block`n"
    Set-Alias -Name $AliasName -Value pichu -ErrorAction SilentlyContinue
    $script:aliasAdded = $true
}

function Write-Summary {
    Write-Output ""

    if ($pathModified) {
        Write-Output "Path environment variable modified; restart your shell to use the new value."
    }
    elseif ($pathAlreadyPresent) {
        Write-Output "Path already contains install directory."
    }
    elseif ($pathSkipped) {
        Write-Output "Path modification skipped (--no-modify-path)."
    }
    else {
        Write-Output "Path was not modified automatically."
    }

    if (-not [string]::IsNullOrWhiteSpace($Alias)) {
        if ($aliasAdded) {
            Write-Output "Command line alias added: `"$Alias`""
        }
        elseif ($aliasAlreadyPresent) {
            Write-Output "Alias `"$Alias`" already configured."
        }
        elseif ($aliasConflict) {
            Write-Output "Alias `"$Alias`" already exists with a different definition."
        }
        else {
            Write-Output "Alias `"$Alias`" was not configured automatically."
        }
    }

    if ((Get-Command pichu -ErrorAction SilentlyContinue) -or $pathModified) {
        Write-Output "Successfully installed. You can run `"pichu`" from anywhere."
    }
    else {
        Write-Output "Successfully installed, but 'pichu' is not currently on PATH in this shell."
    }

    if (-not (Get-Command pichu -ErrorAction SilentlyContinue)) {
        Write-Output ""
        Write-Output "Manual PATH command (PowerShell):"
        Write-Output "  `$env:Path = `"$pathTargetDir;`$env:Path`""
        Write-Output "User PATH target:"
        Write-Output "  $pathTargetDir"
    }
}

if ($Help) {
    Show-Usage
    exit 0
}

Test-AliasName $Alias

$versionText = Get-PythonVersion
if ([string]::IsNullOrWhiteSpace($versionText)) {
    Fail "Python 3.11+ is required. Install it first."
}

$versionParts = $versionText.Trim().Split(".")
if ($versionParts.Length -lt 2) {
    Fail "Unable to parse Python version '$versionText'."
}

$major = [int]$versionParts[0]
$minor = [int]$versionParts[1]
if ($major -lt 3 -or ($major -eq 3 -and $minor -lt 11)) {
    Fail "Python 3.11+ is required (found $versionText)."
}

$installer = Select-Installer
if ([string]::IsNullOrWhiteSpace($installer)) {
    Fail "No package installer found. Install uv manually from https://docs.astral.sh/uv/getting-started/installation/."
}

Install-Pichu $installer

$pichuPath = Get-CommandPath "pichu"
if ($pichuPath) {
    $pathTargetDir = Split-Path -Parent $pichuPath
}

Ensure-PathEntry -Dir $pathTargetDir -Skip:$NoModifyPath
Ensure-AliasEntry $Alias
Write-Summary

if ($pathError -or $aliasError) {
    exit 1
}
