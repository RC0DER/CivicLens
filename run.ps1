# One command to bring CivicLens up locally on Windows.
#   .\run.ps1            → http://127.0.0.1:8000
#   .\run.ps1 -Fresh     → wipe local data first
param([switch]$Fresh)
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "backend")

# Note: a virtualenv nested very deep in the filesystem can break the
# cryptography DLL load on Windows. Set $env:VENV to something short if so.
$venv = if ($env:VENV) { $env:VENV } else { ".venv" }
if (-not (Test-Path $venv)) {
  Write-Host "-> creating virtualenv"
  python -m venv $venv
}
$py = Join-Path $venv "Scripts\python.exe"

Write-Host "-> installing dependencies"
& $py -m pip install -q --upgrade pip
& $py -m pip install -q -r requirements.txt

if (-not (Test-Path ".env")) {
  Write-Host "-> writing .env with freshly generated keys"
  $keys = (& $py -c "from app.security import generate_intake_keypair; print('{0} {1}'.format(*generate_intake_keypair()))") -split  
  $seal = $keys[0]; $open = $keys[1]
  $hmac = & $py -c "import secrets; print(secrets.token_urlsafe(48))"
  $jwt  = & $py -c "import secrets; print(secrets.token_urlsafe(48))"
  @"
ENV=development
PROFILE=all
CASE_DB_URL=sqlite:///./data/case.db
INTAKE_DB_URL=sqlite:///./data/intake.db
INTAKE_HMAC_KEY=$hmac
INTAKE_SEAL_KEY=$seal
INTAKE_OPEN_KEY=$open
JWT_SECRET=$jwt
STORAGE_BACKEND=local
EVIDENCE_DIR=./data/evidence
SERVE_FRONTEND=true
DEMO_MODE=true
"@ | Out-File -FilePath ".env" -Encoding utf8
}

if ($Fresh -and (Test-Path "data")) { Write-Host "-> clearing local data"; Remove-Item -Recurse -Force "data" }
if (-not (Test-Path "data")) { New-Item -ItemType Directory "data" | Out-Null }

Write-Host "-> applying migrations"
& $py -m alembic upgrade head | Out-Null
& $py -m alembic --name intake upgrade head | Out-Null

$count = & $py -c "from app.db import CaseSession; from app.models import Case; db=CaseSession(); print(db.query(Case).count()); db.close()"
if ($count -eq "0") { Write-Host "-> seeding the register"; & $py -m scripts.seed }

Write-Host ""
Write-Host "CivicLens is starting on http://127.0.0.1:8000"
Write-Host "Sign-in credentials for the staff views are shown on the Official Access page."
Write-Host ""
& $py -m uvicorn app.main:app --host 127.0.0.1 --port 8000
