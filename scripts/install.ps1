<#
.SYNOPSIS
    Install BackyUpy into a local virtual environment.

.DESCRIPTION
    Creates (or reuses) a .venv next to this script, upgrades pip, and installs
    BackyUpy with the GUI, LLM and content-extraction extras. Safe to re-run.
#>
[CmdletBinding()]
param(
    [switch]$NoUi,
    [switch]$Dev
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot
try {
    Write-Host "BackyUpy installer" -ForegroundColor Cyan

    if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
        throw "Python was not found on PATH. Install Python 3.10+ from https://python.org and re-run."
    }

    $venv = Join-Path $repoRoot ".venv"
    if (-not (Test-Path $venv)) {
        Write-Host "Creating virtual environment..." -ForegroundColor Yellow
        python -m venv $venv
    }

    $pythonExe = Join-Path $venv "Scripts\python.exe"
    & $pythonExe -m pip install --upgrade pip

    $extras = @("llm", "extract")
    if (-not $NoUi) { $extras += "ui" }
    if ($Dev)      { $extras += "dev" }
    $extraSpec = "[" + ($extras -join ",") + "]"

    Write-Host "Installing backyupy$extraSpec ..." -ForegroundColor Yellow
    & $pythonExe -m pip install -e "."

    # Install the optional extras explicitly so a locked-down resolver that
    # ignores extras still ends up with a working environment.
    $optional = @("requests", "pypdf", "python-docx", "openpyxl")
    if (-not $NoUi) { $optional += "PySide6" }
    if ($Dev)       { $optional += @("pytest", "pytest-cov") }
    & $pythonExe -m pip install @optional

    Write-Host ""
    Write-Host "Done." -ForegroundColor Green
    Write-Host "Activate with:  .\.venv\Scripts\Activate.ps1"
    Write-Host "Start the GUI:  .\scripts\run_gui.bat"
    Write-Host "Start the CLI:  .\scripts\run_cli.bat --help"
}
finally {
    Pop-Location
}
