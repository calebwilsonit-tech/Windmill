# Windmill installer for Windows.
# Right-click this file -> "Run with PowerShell"   (or in PowerShell:  powershell -ExecutionPolicy Bypass -File install.ps1)
$ErrorActionPreference = "Stop"
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path

function Say($m) { Write-Host "  $m" -ForegroundColor Cyan }
function Ok($m)  { Write-Host "  OK  $m" -ForegroundColor Green }

Write-Host ""
Write-Host "        \\                //" -ForegroundColor Cyan
Write-Host "          \\     __     //" -ForegroundColor Cyan
Write-Host "              (_()_)" -ForegroundColor Cyan
Write-Host "            //  |  |  \\" -ForegroundColor Cyan
Write-Host "               /|__|\" -ForegroundColor Yellow
Write-Host "          W I N D M I L L   installer"
Write-Host ""

# 1. Python
$py = $null
foreach ($c in @("py", "python", "python3")) {
    $cmd = Get-Command $c -ErrorAction SilentlyContinue
    if ($cmd -and $cmd.Source -notlike "*WindowsApps*") { $py = $cmd.Source; break }
}
if (-not $py) {
    Say "Python isn't installed - installing it (Windows may ask for permission)..."
    winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "User") + ";" + [Environment]::GetEnvironmentVariable("Path", "Machine")
    $py = (Get-Command python -ErrorAction SilentlyContinue).Source
    if (-not $py) { Write-Host "Please reopen PowerShell and run this installer again." -ForegroundColor Yellow; exit 1 }
}
Ok "Python: $py"

# 2. Git
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Say "Git isn't installed - installing it..."
    winget install -e --id Git.Git --accept-source-agreements --accept-package-agreements
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "User") + ";" + [Environment]::GetEnvironmentVariable("Path", "Machine")
}
Ok "Git"

# 3. `windmill` command on PATH
$Bin = Join-Path $env:USERPROFILE ".windmill\bin"
New-Item -ItemType Directory -Force -Path $Bin | Out-Null
$Shim = Join-Path $Bin "windmill.cmd"
"@echo off`r`n`"$py`" `"$Here\windmill.py`" %*" | Set-Content -Encoding ASCII $Shim
$UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($UserPath -notlike "*$Bin*") {
    [Environment]::SetEnvironmentVariable("Path", "$UserPath;$Bin", "User")
    $env:Path = "$env:Path;$Bin"
}
Ok "The 'windmill' command is installed."

# 4. Guided setup (AI engine, model, library)
Write-Host ""
& $py "$Here\windmill.py" setup
Write-Host ""
Say "From now on, open any terminal and type:  windmill"
Read-Host "Press Enter to close"
