<#
One-command setup on Windows (PowerShell 5.1+). Needs Python 3.11+ and Docker Desktop.

  powershell -ExecutionPolicy Bypass -File scripts\setup.ps1

1. creates .venv and installs the package (dev + api extras)
2. writes .env with random passwords if it does not exist
3. starts Postgres with docker compose and waits until it is healthy
4. builds and loads the incidents from configs/incident_plan.yaml

Afterwards, in the same window:  . .\scripts\load_env.ps1
#>
$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $Root

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "creating .venv"
    python -m venv .venv
}
& .venv\Scripts\python -m pip install -q --disable-pip-version-check -e ".[dev,api]"
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

& .venv\Scripts\python scripts\make_env.py
. (Join-Path $PSScriptRoot "load_env.ps1")

Write-Host "starting Postgres (docker compose)"
docker compose up -d --wait db
if ($LASTEXITCODE -ne 0) { throw "docker compose failed; is Docker Desktop running?" }

Write-Host "loading incidents"
& .venv\Scripts\python -m investigator.generator
if ($LASTEXITCODE -ne 0) { throw "loading incidents failed" }

Write-Host ""
Write-Host "Done. Next, in this window:"
Write-Host "  . .\scripts\load_env.ps1"
Write-Host "  .venv\Scripts\python -m pytest tests/unit                  # tests"
Write-Host "  .venv\Scripts\python scripts\run_eval.py --label eval-v4 --pattern exchange --company-context --cache-mode off   # evaluation (uses Claude quota)"
Write-Host "  .venv\Scripts\python -m investigator.web --label eval-v4   # viewer on http://127.0.0.1:8000"
