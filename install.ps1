# Aigator quick installer for Windows PowerShell 5.1+ and PowerShell 7.
# Usage: iex (irm https://raw.githubusercontent.com/tiborsekera/aigator/main/install.ps1)
& {
$ErrorActionPreference = "Stop"
Write-Host "Installing Aigator..."

# Resolve the actual interpreter, including when discovered via the py launcher.
$pythonExe = $null
foreach ($cmd in @("python", "python3", "py")) {
    if (Get-Command $cmd -ErrorAction SilentlyContinue) {
        try {
            $probe = & $cmd -I -c "import sqlite3,sys; assert sys.version_info >= (3,10); sqlite3.connect(':memory:').execute('CREATE VIRTUAL TABLE t USING fts5(x)'); print(sys.executable)" 2>$null
            if ($LASTEXITCODE -eq 0 -and $probe) { $pythonExe = [string]$probe; break }
        } catch { continue }
    }
}
if (-not $pythonExe) { throw "Python 3.10+ with SQLite FTS5 is required. Install Python from python.org, then rerun this installer." }

$installDir = if ($env:AIGATOR_INSTALL_DIR) { $env:AIGATOR_INSTALL_DIR } else { Join-Path $env:LOCALAPPDATA "aigator\app" }
$binDir = if ($env:AIGATOR_BIN_DIR) { $env:AIGATOR_BIN_DIR } else { Join-Path $env:LOCALAPPDATA "aigator\bin" }
$installDir = [IO.Path]::GetFullPath($installDir).TrimEnd('\')
$binDir = [IO.Path]::GetFullPath($binDir).TrimEnd('\')
if ($binDir -eq $installDir -or $binDir.StartsWith($installDir + '\', [StringComparison]::OrdinalIgnoreCase)) {
    throw "Bin directory must be outside the application directory."
}
# cmd.exe expands percent characters even inside quotes. Fail before changing files.
foreach ($path in @($installDir, $binDir, $pythonExe)) {
    if ($path -match '[%\r\n"]') { throw "Installation paths cannot contain percent signs, quotes, or newlines." }
}
if (Test-Path -LiteralPath $installDir) {
    $item = Get-Item -LiteralPath $installDir -Force
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
        -not (Test-Path -LiteralPath "$installDir\.aigator-install" -PathType Leaf)) {
        throw "Refusing unmarked or linked existing target: $installDir. Existing files are preserved."
    }
}
$wrapper = Join-Path $binDir "aigator.cmd"
if (Test-Path -LiteralPath $wrapper) {
    $item = Get-Item -LiteralPath $wrapper -Force
    if ($item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw "Refusing non-regular launcher: $wrapper" }
    $oldText = Get-Content -LiteralPath $wrapper -Raw
    $legacyLine = 'set "PYTHONPATH=' + $installDir + ';%PYTHONPATH%"'
    if (-not $oldText.Contains('rem aigator managed launcher v1') -and -not $oldText.Contains($legacyLine)) {
        throw "Refusing unrelated launcher: $wrapper"
    }
}
New-Item -ItemType Directory -Path $binDir -Force | Out-Null
$parent = Split-Path $installDir
New-Item -ItemType Directory -Path $parent -Force | Out-Null
$stage = Join-Path $parent ('.aigator-stage.' + [guid]::NewGuid())
New-Item -ItemType Directory -Path $stage | Out-Null
$launchStage = Join-Path $binDir ('.aigator-launcher.' + [guid]::NewGuid() + '.cmd')
$backup = $null
try {
    $stagedApp = Join-Path $stage "app"
    if (Get-Command git -ErrorAction SilentlyContinue) {
        & git clone --depth 1 --quiet "https://github.com/tiborsekera/aigator.git" $stagedApp
        if ($LASTEXITCODE -ne 0) { throw "Git clone failed; previous installation preserved." }
    } else {
        [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
        $zipPath = Join-Path $stage "source.zip"
        Invoke-WebRequest -Uri "https://github.com/tiborsekera/aigator/archive/refs/heads/main.zip" -OutFile $zipPath -UseBasicParsing -TimeoutSec 180
        Expand-Archive -LiteralPath $zipPath -DestinationPath $stage
        Move-Item -LiteralPath (Join-Path $stage "aigator-main") -Destination $stagedApp
    }
    if (-not (Test-Path -LiteralPath "$stagedApp\aigator\cli.py" -PathType Leaf)) { throw "Incomplete download." }
    $runCode = "import runpy,sys; sys.path.insert(0,sys.argv.pop(1)); runpy.run_module('aigator.cli',run_name='__main__')"
    & $pythonExe -I -c $runCode $stagedApp --help | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Installation validation failed." }
    Set-Content -LiteralPath "$stagedApp\.aigator-install" -Value "aigator managed installation v1" -Encoding ASCII
    # cmd.exe reads its active code page; reject unrepresentable paths instead of
    # silently producing a broken launcher (Windows PowerShell defaults to UTF-16).
    $cmdContent = "@echo off`r`nrem aigator managed launcher v1`r`nsetlocal DisableDelayedExpansion`r`n`"$pythonExe`" -I -c `"$runCode`" `"$installDir`" %*`r`nexit /b %errorlevel%`r`n"
    $encoding = [Console]::OutputEncoding
    if ($encoding.GetString($encoding.GetBytes($cmdContent)) -ne $cmdContent) {
        throw "Installation paths are not representable in the console code page. Use ASCII paths or a UTF-8 console."
    }
    [IO.File]::WriteAllBytes($launchStage, $encoding.GetBytes($cmdContent))
    if (Test-Path -LiteralPath $installDir) {
        $backup = "$installDir.backup.$([guid]::NewGuid())"
        Move-Item -LiteralPath $installDir -Destination $backup
    }
    try {
        Move-Item -LiteralPath $stagedApp -Destination $installDir
        if (Test-Path -LiteralPath $wrapper) {
            [IO.File]::Replace($launchStage, $wrapper, $null)
        } else { [IO.File]::Move($launchStage, $wrapper) }
    } catch {
        if (Test-Path -LiteralPath $installDir) { Move-Item -LiteralPath $installDir -Destination $stagedApp }
        if ($backup) { Move-Item -LiteralPath $backup -Destination $installDir }
        throw
    }
    if ($backup) { Write-Host "Previous installation preserved at $backup" }
} finally {
    Remove-Item -LiteralPath $stage -Recurse -Force
    if (Test-Path -LiteralPath $launchStage) { Remove-Item -LiteralPath $launchStage -Force }
}

# Compare PATH entries, not substrings; also refresh an already-configured shell.
$userPath = [string][Environment]::GetEnvironmentVariable("Path", "User")
try {
    if ($env:AIGATOR_NO_MODIFY_PATH -ne '1' -and ($userPath -split ';') -notcontains $binDir) {
        [Environment]::SetEnvironmentVariable("Path", ($userPath.TrimEnd(';') + ';' + $binDir).TrimStart(';'), "User")
    }
} catch { Write-Warning "Installed, but could not update User PATH. Add $binDir manually." }
if (($env:Path -split ';') -notcontains $binDir) { $env:Path += ";$binDir" }
Write-Host "Installed launcher: $wrapper"
Write-Host "Run aigator daemon to start the dashboard and local sync."
Write-Host "Browser extension folder: $installDir\extension"
Write-Host "Load it in Chrome/Brave and pair once in the popup for automatic capture."
Write-Host "The installer does not start a daemon or configure login startup."
}
