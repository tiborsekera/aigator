# Aigator — 1-Line Quick Installer for Windows PowerShell
# Usage: iex (irm https://raw.githubusercontent.com/tiborsekera/aigator/main/install.ps1)

$ErrorActionPreference = "Stop"

Write-Host "`n🐊 Installing Aigator (Local AI Session Aggregator)...`n" -ForegroundColor Cyan

# 1. Check Python 3.10+
$pythonCmd = $null
foreach ($cmd in @("python", "python3", "py")) {
    if (Get-Command $cmd -ErrorAction SilentlyContinue) {
        $testVersion = & $cmd -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
        if ($testVersion -and [version]$testVersion -ge [version]"3.10") {
            $pythonCmd = $cmd
            break
        }
    }
}

if (-not $pythonCmd) {
    Write-Error "Python 3.10 or higher is required. Please install Python from https://www.python.org or via 'winget install Python.Python.3.12'."
    exit 1
}

$pyVer = & $pythonCmd -c "import sys; print(sys.version.split()[0])"
Write-Host "  • Found Python: $pythonCmd ($pyVer)" -ForegroundColor White

# 2. Target Directories
$installDir = "$env:LOCALAPPDATA\aigator\app"
$binDir = "$env:LOCALAPPDATA\aigator\bin"

& $pythonCmd -c "import sqlite3; c=sqlite3.connect(':memory:'); c.execute('CREATE VIRTUAL TABLE t USING fts5(x)')"
if ($LASTEXITCODE -ne 0) { throw "Python SQLite FTS5 support is required." }
if ((Test-Path $installDir) -and (-not (Test-Path "$installDir\.aigator-install"))) {
    throw "Refusing unmarked existing target: $installDir. Move it aside first; no files were changed."
}
New-Item -ItemType Directory -Path $binDir -Force | Out-Null
$stage = Join-Path (Split-Path $installDir) ([guid]::NewGuid().ToString())
New-Item -ItemType Directory -Path $stage | Out-Null
try {
    $stagedApp = Join-Path $stage "app"
    if (Get-Command git -ErrorAction SilentlyContinue) {
        git clone --depth 1 --quiet "https://github.com/tiborsekera/aigator.git" $stagedApp
        if ($LASTEXITCODE -ne 0) { throw "Git clone failed; previous installation preserved." }
    } else {
        $zipPath = Join-Path $stage "source.zip"
        Invoke-WebRequest -Uri "https://github.com/tiborsekera/aigator/archive/refs/heads/main.zip" -OutFile $zipPath -UseBasicParsing
        Expand-Archive -Path $zipPath -DestinationPath $stage
        Move-Item (Join-Path $stage "aigator-main") $stagedApp
    }
    if (-not (Test-Path "$stagedApp\aigator\cli.py")) { throw "Incomplete download." }
    Push-Location $stagedApp
    try {
        & $pythonCmd -m aigator.cli --help | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Installation validation failed." }
    } finally { Pop-Location }
    Set-Content "$stagedApp\.aigator-install" "aigator managed installation v1"
    $backup = $null
    if (Test-Path $installDir) {
        $backup = "$installDir.backup.$([guid]::NewGuid())"
        Move-Item $installDir $backup
    }
    try { Move-Item $stagedApp $installDir }
    catch {
        if ($backup) { Move-Item $backup $installDir }
        throw
    }
    if ($backup) { Write-Host "Previous installation preserved at $backup" }
} finally { Remove-Item $stage -Recurse -Force }

# 4. Create aigator.cmd in binDir
$cmdContent = @"
@echo off
set "PYTHONPATH=$installDir;%PYTHONPATH%"
"$((Get-Command $pythonCmd).Source)" -m aigator.cli %*
"@

Set-Content -Path "$binDir\aigator.cmd" -Value $cmdContent
Write-Host "  • Installed binary: $binDir\aigator.cmd" -ForegroundColor White

# 5. Add to User PATH if not present
$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($userPath -notlike "*$binDir*") {
    [Environment]::SetEnvironmentVariable("Path", "$userPath;$binDir", "User")
    $env:Path += ";$binDir"
    Write-Host "  • Added $binDir to User PATH." -ForegroundColor Green
}

Write-Host "`n🎉 Aigator installed successfully!`n" -ForegroundColor Green
Write-Host "Quickstart commands:" -ForegroundColor White
Write-Host "  aigator daemon       # Start local web app on http://127.0.0.1:8765" -ForegroundColor Cyan
Write-Host "  aigator copilot      # Sync active VS Code Copilot chats" -ForegroundColor Cyan
Write-Host "  aigator search <q>   # Fast BM25 keyword search" -ForegroundColor Cyan
Write-Host "  aigator --help       # View all CLI commands`n" -ForegroundColor Cyan
