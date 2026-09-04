# Create a desktop shortcut for running Crypto Investigator FROM SOURCE
# (the installed build creates its own shortcuts; this is for developers
# and agencies running the Python checkout).
#
#   powershell -ExecutionPolicy Bypass -File tools\make_shortcut.ps1
#
# The shortcut runs pythonw.exe with run.pyw, so no console window appears.
# Windows will not show shortcut icons from a network path, so the icon is
# copied to %LOCALAPPDATA%\CryptoInvestigator first.

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)

$pythonw = $null
foreach ($candidate in @("pythonw.exe", "pythonw")) {
    $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
    if ($cmd) { $pythonw = $cmd.Source; break }
}
if (-not $pythonw) {
    $py = Get-Command python -ErrorAction SilentlyContinue
    if ($py) { $pythonw = Join-Path (Split-Path $py.Source) "pythonw.exe" }
}
if (-not $pythonw -or -not (Test-Path $pythonw)) {
    throw "pythonw.exe not found. Install Python 3.10+ and make sure it is on PATH."
}

$iconDir = Join-Path $env:LOCALAPPDATA "CryptoInvestigator"
New-Item -ItemType Directory -Force $iconDir | Out-Null
$iconLocal = Join-Path $iconDir "crypto_investigator.ico"
Copy-Item (Join-Path $root "assets\crypto_investigator.ico") $iconLocal -Force

$desktop = [Environment]::GetFolderPath("Desktop")
$linkPath = Join-Path $desktop "Crypto Investigator.lnk"
$shell = New-Object -ComObject WScript.Shell
$link = $shell.CreateShortcut($linkPath)
$link.TargetPath = $pythonw
$link.Arguments = '"' + (Join-Path $root "run.pyw") + '"'
$link.WorkingDirectory = $root
$link.IconLocation = $iconLocal
$link.Description = "Crypto Investigator - trace cryptocurrency and prepare legal process"
$link.Save()

Write-Host "Created $linkPath"
Write-Host "  target: $pythonw run.pyw   (no console window)"
