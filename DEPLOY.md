# Putting CivicLens on the internet

The application is finished and runs as a real website locally. What remains is
hosting, a domain, and a TLS certificate — none of which I can create on your
behalf, because they need your accounts and your payment details.

This is the shortest honest path from here to a public URL.

---

## Run it locally first (2 minutes, no accounts needed)

```bash
./run.sh            # macOS / Linux / Git Bash
.\run.ps1           # Windows PowerShell
```

Then open **http://127.0.0.1:8000**. It creates a virtualenv, generates its own
keys, migrates both databases, seeds the register and serves the portal.

To reach it from your phone on the same Wi-Fi, change the last line of the
script to `--host 0.0.0.0` and visit `http://<your-computer's-IP>:8000`.

---

## Option A — a single small server (recommended)

Best fit: this is one Docker Compose file, it has a real database, and it costs
roughly ₹400–900 a month.

**What I need from you**

| Thing | Why | Where to get it |
|---|---|---|
| A VPS, 2 GB RAM | Runs the four containers | Hetzner CX22 (~€4), DigitalOcean ($6), AWS Lightsail ($5) |
| SSH access | To deploy | Provider's console |
| A domain | HTTPS needs a name | Namecheap / Cloudflare / GoDaddy (~₹800/yr) |

**Then:**

```bash
# on the server
git clone <your repo> civiclens && cd civiclens
cp backend/.env.example .env        # fill in the production block

python3 -c "import secrets; print(secrets.token_urlsafe(48))"   # JWT_SECRET
python3 -c "import secrets; print(secrets.token_urlsafe(48))"   # INTAKE_HMAC_KEY
python3 -c "from app.security import generate_intake_keypair; seal, open_ = generate_intake_keypair(); print('INTAKE_SEAL_KEY', seal); print('INTAKE_OPEN_KEY', open_)"

docker compose up -d --build
docker compose exec public alembic upgrade head
docker compose exec investigator alembic --name intake upgrade head
```

Point the domain's A record at the server, then put Caddy in front for
automatic TLS — two lines:

```
api.yourdomain.org {
    reverse_proxy localhost:8000
}
```

Caddy obtains and renews the certificate itself. If you prefer nginx,
`backend/deploy/nginx.conf` is written and includes the `access_log off` rules
that matter here — but you must add certbot yourself.

## Option B — a managed platform (fastest, no server admin)

Render, Railway or Fly.io will build the Dockerfile and give you HTTPS on a
subdomain immediately.

**What I need from you:** an account on one of them, and a Postgres add-on
(their free tiers work). Then set the environment variables from the
production block of `.env.example` in their dashboard.

Caveat worth knowing: their free tiers sleep after inactivity and some log
client IPs at their edge, outside your control. For a demo that is fine. For
real reporters it is not — see the note below.

## Option C — I hand you a deploy script

Tell me the provider and I will write the exact deploy script, the Caddyfile or
nginx vhost, the systemd units for the scheduled jobs, and a GitHub Actions
workflow that deploys on push. You run one command.

---

## Before real reporters use it

A public demo and a live whistleblowing service are different products. If this
is going to carry genuine reports, the following stop being optional:

1. **`ENV=production` and `DEMO_MODE=false`.** The config refuses to start
   otherwise — demo mode hands out working one-time codes.
2. **Verify the edge does not log client addresses.** `deploy/nginx.conf` does
   this; a managed platform may not let you. This is the single most important
   operational control, and the application cannot enforce it for itself.
3. **A Tor onion service.** Otherwise anyone who can watch a reporter's network
   knows they visited a corruption-reporting site, whatever the site itself does.
4. **Real accounts for officials** via your SSO, not the seeded table.
5. **A penetration test**, and legal sign-off on publishing allegations
   (`docs/THREAT_MODEL.md` and the README explain the exposure).

Until those are done, label the deployment a demonstration — the footer already
says so, and `/health/ready` reports the deployment's actual posture.

---

## What I need to proceed

Pick one and tell me:

- **"Render/Railway/Fly"** → I will write the platform config and the exact
  environment variables to paste in.
- **"I have a VPS at <provider>"** → I will write the deploy script, the TLS
  config and the job timers.
- **"Just make it shareable now"** → I will set up a tunnel (Cloudflare Tunnel
  or ngrok) so the site on your machine gets a public HTTPS URL in about a
  minute. Good for showing people; not a production deployment.

You said you can provide anything — the only things I actually need are an
account on a host, a domain if you want a real name, and your say-so on which
of the three above.
