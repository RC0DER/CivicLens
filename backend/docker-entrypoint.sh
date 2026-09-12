#!/usr/bin/env sh
# Container entrypoint.
#
#   serve   migrate, optionally seed, then run the web server   (default)
#   jobs    run the scheduled sweeps in a loop
#   shell   drop into a shell for maintenance
#
# Migrations run at start-up rather than in a release phase, because Railway
# and similar platforms have no release phase. Alembic is idempotent, so a
# second replica starting concurrently applies nothing and moves on.
set -eu

log() { echo "[entrypoint] $*"; }

migrate() {
  log "migrating case register"
  alembic upgrade head

  # Intake migrations run only where the intake credentials exist - which, by
  # design, is the investigator profile and single-service deployments.
  if [ -n "${INTAKE_DB_URL:-}${INTAKE_DATABASE_URL:-}" ]; then
    log "migrating intake store"
    alembic --name intake upgrade head
  else
    log "no intake database in this service - skipping (this is expected for PROFILE=dept)"
  fi
}

seed_if_empty() {
  [ "${SEED_ON_START:-false}" = "true" ] || return 0
  count=$(python -c "
from app.db import CaseSession
from app.models import Case
with CaseSession() as db:
    print(db.query(Case).count())
" 2>/dev/null || echo 0)
  if [ "$count" = "0" ]; then
    log "register is empty - seeding demonstration data"
    python -m scripts.seed
  else
    log "register already holds $count case(s) - not seeding"
  fi
}

case "${1:-serve}" in
  serve)
    migrate
    seed_if_empty
    log "starting web server on port ${PORT:-8000}"
    exec gunicorn app.main:app -c gunicorn.conf.py
    ;;
  jobs)
    log "starting scheduled job loop"
    while true; do
      python -m app.jobs assignment_sweep || log "assignment_sweep failed"
      python -m app.jobs session_sweep    || log "session_sweep failed"
      python -m app.jobs retention_sweep  || log "retention_sweep failed"
      sleep "${JOB_INTERVAL_SECONDS:-3600}"
    done
    ;;
  shell) exec /bin/sh ;;
  *)     exec "$@" ;;
esac
