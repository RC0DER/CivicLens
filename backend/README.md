# CivicLens API

Backend for the corruption reporting and transparency platform. FastAPI +
SQLAlchemy, Postgres in production, SQLite for local development.

The privacy guarantees the portal makes to citizens are implemented here as
**architecture**, not as permission checks. That distinction is the whole
design, so it is worth stating plainly before the setup instructions.

## The separation

A departmental officer cannot see who reported them. Not because a filter
hides it — because the process serving them has no route to the data:

| Layer | What it does |
|---|---|
| Two databases | `case_db` holds the register. `intake_db` holds contacts. No foreign key, no shared column, no join is possible. |
| One-way bridge | The intake row's primary key is `HMAC-SHA256(key, case_no)`. Without the key you cannot compute which row belongs to which case. Holding the intake dump alone reveals no case numbers. |
| Encryption at rest | The contact itself is Fernet-sealed under a second key. |
| Deployment profile | `PROFILE=dept` starts **without** the intake URL and without either key. In production, a dept service that is handed them **refuses to boot**. |
| Response projections | `DeptCase` is a separate Pydantic model with `extra="forbid"` and no reporter field. A new column on `Case` does not appear in it. |
| Dates, not timestamps | `filed_on` is a `DATE` in the civic timezone. Filing time to the second identifies a person against counter CCTV; the calendar day is enough for the statutory clock. |
| Evidence ingest | Images are re-encoded from pixel data; PDFs lose `/Info` and `/Metadata`; the uploader's filename is discarded before the DB write. |
| Logs | The formatter redacts phone/email/IP patterns and masks sensitive keys. Intake-route latency is logged as a coarse bucket, because a precise duration is a fingerprint. |

`tests/test_separation.py` and `tests/test_hardening.py` assert each of these,
including one test that parses the departmental router's AST and fails if it so
much as *references* an intake symbol.

## Run it

```bash
python -m venv .venv && . .venv/Scripts/activate   # Linux/macOS: source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
python -c "from app.security import generate_fernet_key; print(generate_fernet_key())"   # paste into INTAKE_ENC_KEY
alembic upgrade head
alembic --name intake upgrade head
python -m scripts.seed
uvicorn app.main:app --reload
```

Interactive docs at http://127.0.0.1:8000/docs (disabled when `ENV=production`).

> **Windows note:** if `cryptography` fails with *"DLL load failed … filename or
> extension is too long"*, the virtualenv is too deep in the filesystem. Create
> it somewhere short, e.g. `python -m venv C:\venvs\civiclens`.

```bash
pytest --cov=app          # 49 tests, 87% coverage
ruff check app scripts tests
mypy app
```

## Production

Three services from one image, differing only in environment. Full variable
lists are in `.env.example`; `docker compose up` wires them together.

```
public        intake + public register        holds the sealing keys
dept          departmental portal            holds NOTHING of intake
investigator  Ombudsman workbench            holds the decryption key
```

`ENV=production` makes `config.py` refuse to start on any of: a development or
short `JWT_SECRET`; SQLite; `PROFILE=all`; a dept service holding intake
credentials; a public or dept service holding the decryption key; local disk
evidence storage; wildcard CORS or trusted hosts; `PUBLISH_NAMES_BEFORE_FINDING`;
metrics without a scrape token.

Operational detail lives in:

- `docs/RUNBOOK.md` — deploys, scheduled jobs, alerts, incident procedures, key rotation
- `docs/THREAT_MODEL.md` — adversaries, mitigations, and the residual risks stated plainly
- `SECURITY.md` — disclosure policy
- `deploy/nginx.conf` — the edge config, including the `access_log off` rules that the application cannot enforce for itself

## API

Public — no authentication anywhere in this group:

| Method | Path | |
|---|---|---|
| POST | `/api/reports` | File a report. Returns the case number and an upload token. |
| POST | `/api/reports/{case_no}/evidence` | Attach a file; metadata stripped on ingest. |
| GET | `/api/cases/{case_no}` | Track any case. Open to everyone. |
| GET | `/api/register` | Search and page the register. |
| GET | `/api/stats` | Totals, status split, median days to assign, overdue count. |
| GET | `/api/heatmap` | Verified reports per 10,000 residents by zone. |
| GET | `/api/ledger`, `/api/ledger/verify` | The hash chain, and a walk that proves it is unbroken. |

Authenticated — Argon2id + TOTP, every read audited:

| Method | Path | Role |
|---|---|---|
| POST | `/api/auth/official/login` · `/logout` | — |
| GET | `/api/auth/me` · `/api/auth/audit` | dept, investigator |
| GET | `/api/dept/summary` · `/cases` · `/cases/{no}` · `/evidence/{sha256}` | dept |
| POST | `/api/dept/cases/{no}/reply` | dept |
| GET | `/api/investigator/queue` · `/overdue` | investigator |
| POST | `/api/investigator/cases/{no}/assign` · `/status` | investigator |
| GET | `/api/investigator/cases/{no}/contact` | investigator, separately audited |

Ops: `/health/live`, `/health/ready`, `/metrics` (token-gated).

Errors are RFC 9457 `application/problem+json` with a `request_id` that matches
the log line.

## Two decisions worth arguing about

**Names are withheld until a finding.** Publishing an identifiable official's
name against an unproven allegation is actionable in India (s.499/500 IPC, and
civil suits). The name is collected, stored, and shown to the investigator and
the department — but `PublicCase.accused_name` returns `None` until an
investigator confirms the case and explicitly sets `publish_accused_name`.
Production refuses to start with the override enabled; removing that guard
should be a deliberate, reviewed code change after taking counsel.

**Rate limiting records nothing about the reporter.** The intake bucket is
`HMAC(department|pin)` — it slows a campaign against one office without
learning anything about who is filing. The read-route bucket hashes the client
address with a per-process salt that is never persisted, and the intake route
has no client bucket at all. Rate limiting by identity would defeat the
platform's purpose.

## Before this goes near real reporters

Implemented here · not implemented here.

- [x] Two-store separation, one-way token, encryption at rest, boot-time guards
- [x] Metadata stripping (EXIF/XMP/PDF), filename discard, magic-byte typing
- [x] Argon2id, TOTP with replay prevention, server-side lockout, token revocation
- [x] Audit log, department scoping, 404-not-403 on cross-department reads
- [x] Hash-chain ledger with public verification
- [x] 14-day statutory clock, 90-day retention sweep, session sweep
- [x] Alembic migrations for both stores, with a test that fails on model drift
- [x] Structured JSON logs with PII redaction; Prometheus metrics with no identifying labels
- [x] Problem+JSON errors, security headers, body caps, CORS and host allow-lists
- [x] S3 storage with signed expiring URLs; hardened non-root container; CI with lint, types and coverage gate
- [ ] **Reverse proxy must not log client IPs** — `deploy/nginx.conf` does this, but verify it on every deploy. The app cannot unwrite the proxy's log.
- [ ] Tor onion service — the largest remaining gap in the threat model
- [ ] Real SSO/LDAP for officials; the `officials` table is a stand-in
- [ ] hCaptcha or proof-of-work on submit
- [ ] Hindi and regional language APIs; SMS/IVR intake
- [ ] Third-party penetration test against the separation guarantee
- [ ] DPDP Act 2023 compliance review and a named Data Protection Officer
