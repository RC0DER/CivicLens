# Runbook

## Deploy

```bash
alembic upgrade head                    # case register
alembic --name intake upgrade head      # intake store, from the investigator host only
docker compose up -d --build
curl -fsS https://api.civiclens.gov.example/health/ready | jq
```

`/health/ready` is the deployment gate. Check three fields on every service:

| Field | public | dept | investigator |
|---|---|---|---|
| `profile` | public | dept | investigator |
| `holds_intake_keys` | true | **false** | true |
| `publishes_names_before_finding` | false | false | false |

`holds_intake_keys: true` on the dept service means the privacy firewall is
open. That service will refuse to start in production, so if you see it, you
are not running with `ENV=production`. Stop and fix the deployment.

## Scheduled jobs

| Job | Cadence | What it does |
|---|---|---|
| `python -m app.jobs assignment_sweep` | hourly | Publishes every breach of the 14-day assignment limit to the public ledger |
| `python -m app.jobs session_sweep` | hourly | Clears expired revocation and one-time-code rows |
| `python -m app.jobs retention_sweep` | daily | Destroys contacts past 90 days (investigator host only) |

A missed `retention_sweep` is a compliance incident, not a cosmetic one:
contacts are being held past their retention limit. Alert on it.

## Alerts worth paging for

| Signal | Meaning |
|---|---|
| `civiclens_reports_filed_total` flat for > 2h in business hours | Intake broken. Citizens see failures at the worst moment. |
| `/health/ready` returns 503 | Database unreachable. |
| `civiclens_account_lockouts_total` rising | Credential stuffing against employee codes. |
| `civiclens_contacts_opened_total` above baseline | An investigator is opening contacts unusually often. Review the audit log - this is the metric that catches an insider. |
| `civiclens_assignment_overdue_total` rising | Departments are letting cases lapse. Working as intended; escalate to the Ombudsman. |
| `rate limiter unavailable, failing open` in logs | Redis down; abuse control degraded. Not user-facing. |

## Incident: suspected identity disclosure

Treat as a safety incident, not an IT incident.

1. **Do not delete anything.** The audit log is the evidence.
2. Pull `GET /api/auth/audit` as an investigator, filter `contact_opened` and
   `case_file_opened` for the case, and identify every actor.
3. Check object-store access logs for evidence downloads.
4. Check whether the proxy config still has `access_log off` on `/api/reports`
   and `/api/cases/` - a config drift here is the most likely cause.
5. If a contact was disclosed, notify the reporter through the investigator
   and treat the person as at risk. Escalate to the Ombudsman and the DPO
   within 72 hours (DPDP Act breach notification).

## Incident: database restore

Restore the case register and the intake store **into separate environments**.
A restore that puts both into one host, even briefly, recreates the join this
architecture exists to prevent. Verify after restore:

```bash
psql "$CASE_DB_URL"   -c "\dt" | grep -c intake_contacts   # must be 0
```

## Rotating secrets

| Secret | Rotatable | Notes |
|---|---|---|
| `JWT_SECRET` | Yes | All sessions end; officials sign in again. |
| `METRICS_TOKEN` | Yes | Update the scraper. |
| Database passwords | Yes | Standard. |
| `INTAKE_OPEN_KEY` / `INTAKE_SEAL_KEY` | With a migration | Open-and-reseal every row, on the investigator host. |
| `INTAKE_HMAC_KEY` | **No** | Rotating it orphans every existing contact row - the token cannot be recomputed. Requires a planned re-tokenisation with both keys held simultaneously. |

## Onboarding an official

There is no self-service account creation, by design.

```bash
python -m scripts.manage_official add \
  --employee-code "MCD/REV/2019/0447" \
  --department "Municipal Revenue & Property Tax" \
  --rank "Deputy Commissioner" --role dept
```

Prints a TOTP secret once. Hand it over in person or through the department's
existing credential channel - never by email.

In a real deployment, replace this table with the department's SSO/LDAP: keep
`employee_code`, `department`, `role`, and drop `password_hash`/`totp_secret`
in favour of OIDC claims. The rest of the codebase reads only `Actor`, so the
change is contained to `routers/auth.py` and `deps.py`.
