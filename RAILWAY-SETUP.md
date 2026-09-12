# Your Railway setup — three services, production

Follow this top to bottom. Everything the platform needs is already committed.

Your secrets are **not** in this file. I printed them in the chat when I
generated them — copy them into a password manager now, before you start.
`INTAKE_HMAC_KEY` can never be rotated: it is the one-way bridge between a case
number and its sealed contact, and changing it orphans every existing one.

There are two sealing keys, and which service gets which is the point:
**`INTAKE_SEAL_KEY`** (public half) lets a service seal a contact away, and
**`INTAKE_OPEN_KEY`** (private half) reads one back. The public service gets
only the seal key, so the internet-facing service cannot decrypt the contacts
it collects — not as a matter of policy, but of arithmetic.

---

## 1 · Push to GitHub

```bash
cd "Z:\Projects\CivicLens.com"
gh repo create civiclens --private --source=. --push
```

No GitHub CLI? Create an empty private repo on github.com, then:

```bash
git remote add origin https://github.com/<you>/civiclens.git
git push -u origin main
```

The repo is already committed and `.gitignore` keeps `.env`, the local
databases and the evidence directory out.

## 2 · Object storage (do this before the services)

Railway containers have ephemeral disks — evidence stored locally disappears on
every redeploy, and production refuses to start with `STORAGE_BACKEND=local`
for exactly that reason.

**Cloudflare R2** has a free tier and an S3-compatible endpoint:

1. Cloudflare dashboard → R2 → **Create bucket** → name it `civiclens-evidence`.
2. **Manage R2 API Tokens** → Create token → *Object Read & Write* on that bucket.
3. Note the **Access Key ID**, **Secret Access Key**, and the endpoint
   `https://<account-id>.r2.cloudflarestorage.com`.

Keep the bucket **private**. The application hands out short-lived signed URLs;
a public bucket would make every piece of evidence world-readable.

## 3 · Create the project and databases

1. **railway.app → New Project → Deploy from GitHub repo →** `civiclens`.
   Rename the created service **civiclens-public**.
2. **New → Database → Add PostgreSQL.** Leave it named **Postgres**.
3. **New → Database → Add PostgreSQL** again. Rename it **IntakePostgres**.

Two databases is the point: the case register and the contact store must never
be joinable, and must never be restored into the same environment.

## 4 · Add the other two services

**New → GitHub Repo →** the same repo, twice. Rename them **civiclens-dept**
and **civiclens-investigator**.

Optionally a fourth, **civiclens-jobs**, with *Settings → Deploy → Custom Start
Command* set to `jobs`. Without it the 14-day assignment clock and the 90-day
contact retention sweep never run.

## 5 · Paste the variables

For each service: **Variables → Raw Editor**, paste the block, replace
`<JWT_SECRET>` etc. with the values from the chat, and `<r2-…>` with your R2
credentials. Type the `${{...}}` references exactly as written — Railway
resolves them, so no database password is ever pasted by hand.

### civiclens-public

```
ENV=production
PROFILE=public
PORT=8000
DATABASE_URL=${{Postgres.DATABASE_URL}}
INTAKE_DATABASE_URL=${{IntakePostgres.DATABASE_URL}}
JWT_SECRET=<JWT_SECRET>
INTAKE_HMAC_KEY=<INTAKE_HMAC_KEY>
INTAKE_SEAL_KEY=<INTAKE_SEAL_KEY>
SERVE_FRONTEND=true
DEMO_MODE=false
STORAGE_BACKEND=s3
S3_BUCKET=civiclens-evidence
S3_ENDPOINT_URL=https://<account-id>.r2.cloudflarestorage.com
S3_REGION=auto
AWS_ACCESS_KEY_ID=<r2-access-key-id>
AWS_SECRET_ACCESS_KEY=<r2-secret-access-key>
TRUSTED_HOSTS=*.up.railway.app
CORS_ORIGINS=none
METRICS_TOKEN=<METRICS_TOKEN>
```

### civiclens-dept

```
ENV=production
PROFILE=dept
PORT=8000
DATABASE_URL=${{Postgres.DATABASE_URL}}
JWT_SECRET=<JWT_SECRET>
SERVE_FRONTEND=true
STORAGE_BACKEND=s3
S3_BUCKET=civiclens-evidence
S3_ENDPOINT_URL=https://<account-id>.r2.cloudflarestorage.com
S3_REGION=auto
AWS_ACCESS_KEY_ID=<r2-access-key-id>
AWS_SECRET_ACCESS_KEY=<r2-secret-access-key>
TRUSTED_HOSTS=*.up.railway.app
CORS_ORIGINS=none
METRICS_TOKEN=<METRICS_TOKEN>
```

**Note what is missing: no `INTAKE_DATABASE_URL`, no `INTAKE_HMAC_KEY`, no
`INTAKE_SEAL_KEY`, no `INTAKE_OPEN_KEY`.** That absence *is* the privacy firewall. If you paste one in
by accident the service refuses to boot and tells you why — it fails loudly
rather than quietly exposing reporters.

### civiclens-investigator

```
ENV=production
PROFILE=investigator
PORT=8000
DATABASE_URL=${{Postgres.DATABASE_URL}}
INTAKE_DATABASE_URL=${{IntakePostgres.DATABASE_URL}}
JWT_SECRET=<JWT_SECRET>
INTAKE_HMAC_KEY=<INTAKE_HMAC_KEY>
INTAKE_SEAL_KEY=<INTAKE_SEAL_KEY>
INTAKE_OPEN_KEY=<INTAKE_OPEN_KEY>
SERVE_FRONTEND=true
STORAGE_BACKEND=s3
S3_BUCKET=civiclens-evidence
S3_ENDPOINT_URL=https://<account-id>.r2.cloudflarestorage.com
S3_REGION=auto
AWS_ACCESS_KEY_ID=<r2-access-key-id>
AWS_SECRET_ACCESS_KEY=<r2-secret-access-key>
TRUSTED_HOSTS=*.up.railway.app
CORS_ORIGINS=none
METRICS_TOKEN=<METRICS_TOKEN>
```

### civiclens-jobs (if you added it)

Same as **civiclens-investigator**, plus `JOB_INTERVAL_SECONDS=3600`, and set
the start command to `jobs`.

### Why `CORS_ORIGINS=none`

Each service serves its own copy of the portal, so the page and the API share
an origin and no cross-origin request ever happens. `none` says exactly that,
and it is stricter than naming a hostname. It also means you can deploy before
Railway has assigned a domain — the URL only exists after a successful build,
so requiring it up front would be a deadlock.

If you later put the portal on a different origin than the API, name that
origin here instead.

## 6 · Give the public service a domain

**civiclens-public → Settings → Networking → Generate Domain.** Railway asks
which port the app listens on: enter **8000**, matching the `PORT=8000` in the
variables above. The URL appears in that panel once the deploy succeeds - it
does not exist before the first successful build.

That URL is the citizen-facing site.

Do the same for the other two only if officers need them from outside your
network. If you add a custom domain later, update `TRUSTED_HOSTS` and
`CORS_ORIGINS` on that service to match — production rejects wildcards there.

## 7 · Verify the firewall is closed

```bash
curl https://civiclens-public.up.railway.app/health/ready
curl https://civiclens-dept.up.railway.app/health/ready
```

| Field | public | dept | investigator |
|---|---|---|---|
| `profile` | public | dept | investigator |
| `holds_intake_keys` | true | **false** | true |
| `publishes_names_before_finding` | false | false | false |

`holds_intake_keys: false` on the dept service is the line that matters. If you
ever see `true` there, an intake key has been pasted into the wrong dashboard —
though in `production` that service will simply have failed to start.

## 8 · Create the first officer accounts

The register starts empty — no seeded demo data in production, by design.

Railway dashboard → **civiclens-dept → Deployments → ⋮ → Shell**, or
`railway shell` from the CLI:

```bash
python -m scripts.manage_official add \
  --employee-code "MCD/REV/2019/0447" \
  --department "Municipal Revenue & Property Tax" \
  --rank "Deputy Commissioner" --role dept

python -m scripts.manage_official add \
  --employee-code "OMB/INV/2020/0031" \
  --department "Office of the Ombudsman" \
  --rank "Senior Investigator" --role investigator
```

Each prints a password and a TOTP secret **once**. Enrol the secret in Google
Authenticator or Aegis, and hand the credentials over in person — never by
email or in a ticket.

Department names must match exactly what the report form offers; the list is in
`frontend/app.js` (`DEPARTMENTS`).

---

## What Railway cannot do for you

Stated plainly, because this platform's users can be harmed if it is oversold:

1. **Railway's edge logs client IP addresses**, and you cannot switch that off.
   The application never stores one and `backend/deploy/nginx.conf` shows the
   edge config that completes the guarantee — but on Railway you are trusting
   Railway's retention and their response to legal process.
2. **No onion service.** Anyone able to observe a reporter's own network sees
   that they visited a corruption-reporting site, whatever the site does.
3. **One Postgres role per database.** The public service should hold an
   INSERT-only role on the intake store; Railway gives full access. The
   separation still holds where it counts — the dept service, which is the
   account an accused official actually has, holds nothing.

This is a sound pilot deployment. Before it carries reports from people who
could lose their job or their safety, move to a host you control, run the nginx
config in this repo, add a Tor onion service, and get the penetration test in
`backend/docs/THREAT_MODEL.md`.
