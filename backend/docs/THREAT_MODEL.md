# Threat model

The adversary this system is built against is **not** an anonymous attacker on
the internet. It is a resourceful insider: a department head who wants to know
who reported them, with budget, legal process, colleagues in IT, and time.

Every design decision below follows from taking that adversary seriously. A
platform that only defends against outsiders would be worse than useless here,
because it would tell people they were safe when they were not.

## Assets, in order of what their loss costs

| Asset | If it leaks |
|---|---|
| Reporter identity | Harassment, transfer, violence. Irreversible. |
| Linkage between a case and a contact | Same, one step removed. |
| Evidence metadata (GPS, device, author) | Identifies the reporter without naming them. |
| Timing of a filing | Correlates with counter CCTV or a queue token. |
| Case content before publication | Lets a department destroy records first. |
| Accused's name pre-finding | Defamation exposure; harm to a possibly innocent official. |

## Adversaries

**A1 — Accused official with portal access.** Signs in legitimately, wants the
reporter. *Mitigation:* the dept service has no intake credentials, so there is
no query it could run; `DeptCase` has no reporter field; every file opened is
audited and the department cannot read the audit log of others.

**A2 — Insider with database access.** DBA, or someone who obtained a dump.
*Mitigation:* the case register contains no identifier at all. The intake store
contains opaque HMAC tokens against Fernet ciphertext; both keys live only in
the investigator service's environment. A dump of either store alone is inert,
and the two cannot be joined - no shared column, no foreign key.

**A3 — Insider with infrastructure access.** Reads logs, proxy access logs,
backups. *Mitigation:* nginx `access_log off` on intake and tracking routes; a
log format with no `$remote_addr`, `$http_user_agent`, `$http_referer` or
`$args`; gunicorn access log disabled; the application's own formatter redacts
phone/email/IP patterns and masks sensitive keys; filing time is stored as a
date; intake-route latency is logged as a coarse bucket.

**A4 — Legal process against the operator.** A court orders disclosure of "who
filed CRTP-2026-004417". *Mitigation:* for an anonymous filing there is nothing
to produce - the information was never collected. For a follow-up filing, the
contact can be produced only while it exists; the 90-day retention sweep
destroys it, and the case survives without it.

**A5 — Network observer.** Sees that a citizen's address contacted the portal.
*Mitigation:* TLS, HSTS. **Not fully mitigated** - an onion service is required
and is not yet deployed. Until then the platform should not claim protection
against an adversary who can watch the citizen's own connection.

**A6 — Malicious reporter.** Floods a department, or names an innocent official.
*Mitigation:* per-office throttling that records nothing about the filer;
names withheld from public projections until a finding substantiates them;
s.182 IPC notice on the form; investigator triage before anything is
published as substantiated.

**A7 — Ordinary web attacker.** *Mitigation:* parameterised queries throughout
(no string-built SQL), Pydantic validation with `extra="forbid"`, magic-byte
file typing, size caps, TrustedHost and CORS allow-lists, security headers,
Argon2id, TOTP with replay prevention, server-side lockout, short token
lifetimes with revocation.

## Residual risks — state these plainly, do not paper over them

1. **No onion service.** A6/A5 above. Highest-priority remaining gap.
2. **The operator can correlate at write time.** Anyone who controls the
   running public service could log the request body before it is sealed. Only
   deployment controls (signed images, restricted access, audited config)
   mitigate this; the architecture cannot.
3. **A determined department may identify a reporter from case content.** "The
   shopkeeper on the Central market row" identifies a person regardless of what
   the database holds. Writing guidance on the form is the only mitigation, and
   it is imperfect.
4. **In-process rate limiting is per-worker** where Redis is unavailable.
5. **No formal key-rotation plan.** Rotating `INTAKE_HMAC_KEY` orphans every
   existing contact row.
6. **Not penetration tested.** Required before any real reporter uses this.

## What would change the model

If follow-up contact were dropped entirely - report anonymously or not at all -
A2, A4 and residual risk 5 would disappear, and the intake store with them.
That is a product decision with a real cost to investigations, and it is the
single biggest lever available.
