# Deploying CivicLens to Railway

Everything Railway needs is already in the repo: `Dockerfile`, `railway.json`,
and an entrypoint that migrates the database before the server starts. You do
not need the Railway CLI — the dashboard is enough.

**Pick one shape first.** They differ in honesty, not just effort.

| | One service | Three services |
|---|---|---|
| Time | ~10 minutes | ~25 minutes |
| Cost | one Postgres | two Postgres |
| `ENV` | `staging` | `production` |
| Separation | **logical only** | **architectural** |
| Honest label | demonstration | live service |

The three-service shape is the one this system was designed around: the
departmental service runs without the intake database URL and without either
key, so an accused official's account has no route to reporter contacts. In the
single-service shape one process serves all three surfaces, so that guarantee
rests on the code alone — which is why `ENV=production` refuses to start that
way instead of letting you believe otherwise.

Use one service to show people the platform. Use three before anyone files a
real report.

---

## Step 0 — get the code onto GitHub

Railway deploys from a repository.

```bash
cd <project folder>
git init
git add .
git commit -m "CivicLens: corruption reporting and transparency platform"
gh repo create civiclens --private --source=. --push
```

`.gitignore` already excludes `backend/.env`, the local databases and the
evidence directory. Check `git status` before your first push and confirm no
`.env` is staged.

## Step 1 — generate the three secrets, once

```bash
python -c "import secrets; print('JWT_SECRET      ', secrets.token_urlsafe(48))"
python -c "import secrets; print('INTAKE_HMAC_KEY ', secrets.token_urlsafe(48))"
python -c "from app.security import generate_intake_keypair; seal, open_ = generate_intake_keypair(); print('INTAKE_SEAL_KEY', seal); print('INTAKE_OPEN_KEY', open_)"
```

Save all three in a password manager now. **`INTAKE_HMAC_KEY` cannot be rotated
later** — it is the one-way bridge between a case number and its sealed
contact, and changing it orphans every existing one.

---

## Option 1 — one service (demonstration)

1. **New Project → Deploy from GitHub repo →** pick `civiclens`. Railway
   detects the Dockerfile and starts building.
2. **New → Database → Add PostgreSQL.** Leave it named `Postgres`.
3. Open the **civiclens** service → **Variables** → *Raw editor*, and paste:

   ```
   ENV=staging
   PROFILE=all
   DATABASE_URL=${{Postgres.DATABASE_URL}}
   INTAKE_DATABASE_URL=${{Postgres.DATABASE_URL}}
   JWT_SECRET=<paste>
   INTAKE_HMAC_KEY=<paste>
   INTAKE_SEAL_KEY=<seal key>
   SERVE_FRONTEND=true
   DEMO_MODE=true
   SEED_ON_START=true
   STORAGE_BACKEND=local
   METRICS_ENABLED=false
   TRUSTED_HOSTS=*
   CORS_ORIGINS=*
   ```

   Type the `${{Postgres.DATABASE_URL}}` references exactly as written —
   Railway resolves them, so no password is ever pasted by hand.
4. **Settings → Networking → Generate Domain.**
5. Open the URL. The register is seeded, and the Official Access page shows
   working sign-in credentials with live one-time codes.

Tighten `TRUSTED_HOSTS` and `CORS_ORIGINS` to your actual domain once you have
it (`*.up.railway.app` works as a wildcard).

---

## Option 2 — three services (the real architecture)

1. **New Project → Deploy from GitHub repo →** pick `civiclens`. Rename this
   service **civiclens-public**.
2. **New → Database → Add PostgreSQL** twice. Rename them **Postgres** and
   **IntakePostgres**.
3. **New → GitHub Repo →** same repo, twice more. Name them
   **civiclens-dept** and **civiclens-investigator**.
4. Paste the variables for each service from `railway.env.example`. Read the
   three blocks side by side — the dept service is defined by the three
   variables it does **not** have.
5. Generate a domain for **civiclens-public** (this is the citizen-facing URL).
   Give the other two domains only if officers need them from outside your
   network; otherwise leave them internal.
6. Optional: a fourth service from the same repo with **Start Command `jobs`**
   runs the statutory sweeps — the 14-day assignment clock, the 90-day contact
   retention, and session cleanup. Without it those never run.

Object storage is required in production (`STORAGE_BACKEND=s3`): Railway
containers have ephemeral disks, so locally-stored evidence disappears on
every redeploy. Cloudflare R2 has a free tier and an S3-compatible endpoint;
set `S3_BUCKET`, `S3_ENDPOINT_URL`, `AWS_ACCESS_KEY_ID` and
`AWS_SECRET_ACCESS_KEY`.

---

## After it is up

```bash
curl https://<your-domain>/health/ready
```

Check three fields on each service:

| | public | dept | investigator |
|---|---|---|---|
| `profile` | public | dept | investigator |
| `holds_intake_keys` | true | **false** | true |
| `publishes_names_before_finding` | false | false | false |

`holds_intake_keys: true` on the dept service means the firewall is open. In
`production` that service will not have started at all, so seeing it means you
are not running with `ENV=production`.

Create real officer accounts from the Railway shell:

```bash
railway run --service civiclens-dept -- \
  python -m scripts.manage_official add \
    --employee-code "MCD/REV/2019/0447" \
    --department "Municipal Revenue & Property Tax" \
    --rank "Deputy Commissioner" --role dept
```

It prints a password and a TOTP secret once. Hand them over in person.

---

## What Railway cannot give you

Worth knowing before this carries real reports, because none of it is fixable
from inside the application:

1. **Railway's edge logs client IP addresses.** You cannot turn that off. The
   application never stores one, and `deploy/nginx.conf` shows the edge config
   that completes the guarantee — but on Railway you are trusting Railway. For
   a service whose adversary has legal process, that matters.
2. **No onion service.** Anyone who can observe a reporter's network sees that
   they visited a corruption-reporting site.
3. **Shared Postgres roles.** The public service should have an INSERT-only
   role on the intake database; Railway gives one role per database. The
   separation still holds where it counts — the dept service has no intake
   credentials at all — but it is weaker than the design intends.
4. **Ephemeral disk.** Use object storage, or uploaded evidence vanishes on
   redeploy.

For a public demonstration, a pilot, or a proposal to a municipal body, Railway
is a good fit. For a live whistleblowing service carrying reports from people
who can be harmed, plan to move to a host you control, with the nginx config in
this repo and a Tor onion service.
