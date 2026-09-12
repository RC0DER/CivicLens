# CivicLens

**Corruption Reporting & Transparency Platform** — a working civic
accountability portal. File a corruption report without giving your name,
track any case from intake to penalty, and let departments answer complaints
against them without ever seeing who reported.

```bash
./run.sh          # macOS / Linux / Git Bash
.\run.ps1         # Windows PowerShell
```

Then open **http://127.0.0.1:8000**.

| | |
|---|---|
| `frontend/` | The portal — single page, no build step, talks to the API |
| `backend/` | FastAPI + SQLAlchemy, Postgres in production, SQLite locally |
| `RAILWAY-SETUP.md` | **Step-by-step Railway deployment** — three services, production |
| `DEPLOY.md` | Other hosting options, and what each needs |
| `backend/README.md` | Architecture, API reference, what is and isn't implemented |
| `backend/docs/THREAT_MODEL.md` | Adversaries, mitigations, residual risks |
| `backend/docs/RUNBOOK.md` | Deploys, jobs, alerts, incidents, key rotation |

## What actually works

Nothing here is mocked. Filing a report writes to a database, issues a
permanent case number, seals any contact detail into a *separate* database
under a one-way token, strips EXIF/GPS from uploaded evidence, and appends a
hash-chained ledger entry. The department then sees that case in its portal
with the reporter's fields physically absent from the response.

- **Citizens** — file (no account, ever), attach evidence, track any case,
  search the register, read the density map and the verified bulletins.
- **Departments** — sign in with password + TOTP, read only their own cases,
  publish a reply. Every file opened is audited.
- **Investigators** — queue, assignment (refused inside the accused
  department), findings, and the only route that can open a sealed contact.

## The one idea worth understanding

The privacy guarantee is **architecture, not a permission check**. Reporter
contacts live in a different database, keyed by `HMAC(key, case_no)`, encrypted
at rest. The departmental service runs without that database's URL and without
either key — so there is no query it could run, no matter what a bug or a
malicious change did to its code. In production, a departmental service handed
those credentials by mistake **refuses to boot**.

Read `backend/docs/THREAT_MODEL.md` for the adversary this is built against:
not an anonymous attacker, but a department head with budget, legal process and
colleagues in IT.

## Status

68+ tests, 88% coverage, ruff and mypy clean. Demonstration data; demo mode
hands out live one-time codes so anyone can see the staff views, and production
refuses to start with it enabled.
