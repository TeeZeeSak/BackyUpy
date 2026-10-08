<#
.SYNOPSIS
    Build a standalone BackyUpy GUI executable with PyInstaller.

.DESCRIPTION
    Produces dist\BackyUpy\BackyUpy.exe. PyInstaller cannot cross-compile, so
    run this on Windows. The .venv must already exist (see install.ps1).
#>
[CmdletBinding()]
param(
    [string]$Name = "BackyUpy"
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot
try {
    $venvPy = Join-Path $repoRoot ".venv\Scripts\python.exe"
    if (-not (Test-Path $venvPy)) {
        throw "Virtual environment not found. Run scripts\install.ps1 first."
    }

    & $venvPy -m pip install --upgrade pyinstaller

    $entry = Join-Path $env:TEMP "backyupy_entry.py"
    @"
import multiprocessing
from backyupy.ui.app import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main())
"@ | Set-Content -Encoding UTF8 $entry

    & $venvPy -m PyInstaller `
        --noconfirm `
        --clean `
        --windowed `
        --name $Name `
        --collect-submodules backyupy `
        --hidden-import pypdf `
        --hidden-import docx `
        --hidden-import openpyxl `
        "$entry"

    Write-Host ""
    Write-Host "Built: dist\$Name\$Name.exe" -ForegroundColor Green
}
finally {
    Pop-Location
}
