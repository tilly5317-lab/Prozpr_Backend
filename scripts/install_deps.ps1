# Install all requirements into the project .venv only.
# Using plain `pip install` can target C:\Python312\ and fail with Permission denied on Scripts\.
# Production/Docker uses Dockerfile `pip install -r requirements.txt` inside the image — same deps, no Windows ACL issue.

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$VenvPy = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $VenvPy)) {
    Write-Host 'Creating .venv in repo root...'
    if (Get-Command py -ErrorAction SilentlyContinue) {
        py -3 -m venv (Join-Path $Root '.venv')
    } else {
        python -m venv (Join-Path $Root '.venv')
    }
    $VenvPy = Join-Path $Root '.venv\Scripts\python.exe'
}

Write-Host "Using: $VenvPy"
& $VenvPy -m pip install --upgrade pip
& $VenvPy -m pip install -r (Join-Path $Root 'requirements.txt')
Write-Host 'Done. Activate with: .\.venv\Scripts\Activate.ps1'
Write-Host 'Then run: uvicorn main:app --reload'
