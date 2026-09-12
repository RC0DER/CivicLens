#!/usr/bin/env bash
# One command to bring CivicLens up locally: set up, migrate, seed, serve.
#   ./run.sh            → http://127.0.0.1:8000
#   ./run.sh --fresh    → wipe local data first
set -euo pipefail
cd "$(dirname "$0")/backend"

PY=${PYTHON:-python}
VENV=${VENV:-.venv}

if [ ! -d "$VENV" ]; then
  echo "→ creating virtualenv"
  "$PY" -m venv "$VENV"
fi
BIN="$VENV/bin"; [ -d "$VENV/Scripts" ] && BIN="$VENV/Scripts"

echo "→ installing dependencies"
"$BIN/python" -m pip install -q --upgrade pip
"$BIN/python" -m pip install -q -r requirements.txt

if [ ! -f .env ]; then
  echo "→ writing .env with freshly generated keys"
  KEYS=$("$BIN/python" -c "from app.security import generate_intake_keypair; print('%s %s' % generate_intake_keypair())")
  SEAL=${KEYS%% *}; OPEN=${KEYS##* }
  HMAC=$("$BIN/python" -c "import secrets; print(secrets.token_urlsafe(48))")
  JWT=$("$BIN/python" -c "import secrets; print(secrets.token_urlsafe(48))")
  cat > .env <<ENVFILE
ENV=development
PROFILE=all
CASE_DB_URL=sqlite:///./data/case.db
INTAKE_DB_URL=sqlite:///./data/intake.db
INTAKE_HMAC_KEY=$HMAC
INTAKE_SEAL_KEY=$SEAL
INTAKE_OPEN_KEY=$OPEN
JWT_SECRET=$JWT
STORAGE_BACKEND=local
EVIDENCE_DIR=./data/evidence
SERVE_FRONTEND=true
DEMO_MODE=true
ENVFILE
fi

if [ "${1:-}" = "--fresh" ]; then
  echo "→ clearing local data"
  rm -rf data
fi
mkdir -p data

echo "→ applying migrations"
"$BIN/python" -m alembic upgrade head >/dev/null
"$BIN/python" -m alembic --name intake upgrade head >/dev/null

if [ -z "$("$BIN/python" -c "
from app.db import CaseSession
from app.models import Case
with CaseSession() as db: print(db.query(Case).count() or '')
")" ]; then
  echo "→ seeding the register"
  "$BIN/python" -m scripts.seed
fi

echo
echo "CivicLens is starting on http://127.0.0.1:8000"
echo "Sign-in credentials for the staff views are shown on the Official Access page."
echo
exec "$BIN/python" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
