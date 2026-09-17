# Phase 4D production hardening

Phase 4D hardens ARIA without weakening its evidence or human-review boundaries. Each slice is
independently testable and committed before the next begins.

## 4D.1 source-structure drift quarantine

Monitored detail pages may disappear, redirect to a generic home page, or move to a new HTML
template. These are deterministic contract failures, so retrying the same response cannot repair
them. ARIA now handles them as source drift:

- a detail connector may try up to five selectors, but every selector must be present in the
  immutable repository source-pack definition;
- an optional `detail_final_path_prefixes` contract rejects soft-404 redirects that leave the
  approved detail-page scope;
- missing approved selectors and final-path violations are terminal for that run and are not sent
  through the transient network retry loop;
- the resource is paused for `ARIA_MONITOR_STRUCTURE_DRIFT_PAUSE_MINUTES` (24 hours by default),
  while unrelated source and resource checks continue;
- an append-only `ResourceStructureIncident` records response provenance, content and HTML-shape
  hashes, redirects, selector expectations, and a bounded tag/class sample;
- page text and raw HTML are deliberately excluded from the diagnostic sample; and
- the operator resource page and Django Admin expose the incident and pause deadline.

The JPDP v4 connector contract also requires final responses to remain under
`/ppdpv1/en/akta/`. The two English links observed redirecting to the JPDP home page are therefore
quarantined as drift instead of being mistaken for valid document pages.

Before enabling a replacement selector, update the repository source pack, increment its connector
and pack versions, run the source-pack test suite, apply the pack through its explicit gate, then
re-run only the affected resource. A broad selector such as `main` must not be introduced as an
unreviewed runtime fallback.

## 4D.2 encrypted backup and restore drills

ARIA backups are designed around the evidence boundary rather than only the relational database.
Every backup contains:

- a PostgreSQL custom-format dump and artifact inventory taken from the same exported,
  repeatable-read snapshot;
- a tar archive of every artifact whose recorded backend is the local filesystem, with every byte
  checked against its database SHA-256 before inclusion;
- an inventory of R2 artifacts with IDs, object keys, content types, sizes, and SHA-256 values;
- one age-encrypted bundle; and
- a small plaintext manifest containing sizes and digests, but no password, private key, access
  key, or artifact content.

R2 remains the durable home of artifacts recorded with the `s3` backend; duplicating those objects
inside every database backup would waste storage. The inventory makes those dependencies explicit
and auditable. Locally stored evidence bytes are included because a database-only backup would be
incomplete.

Generate an age X25519 identity offline with `age-keygen`. Put only the printed public `age1...`
recipient in `ARIA_BACKUP_AGE_RECIPIENT`; keep the identity file outside the repository and Docker
volumes. Set `ARIA_BACKUP_UPLOAD_TO_R2=true` to copy the encrypted bundle and manifest under
`backups/database/YYYY/MM/DD/` in the configured private R2 bucket. ARIA checks the uploaded byte
size and SHA-256 object metadata immediately after each upload.

Operational commands:

```text
make backup
make backup-verify MANIFEST=/var/lib/aria/backups/<name>.manifest.json IDENTITY_FILE=/offline/aria-age-key.txt
make restore-drill MANIFEST=/var/lib/aria/backups/<name>.manifest.json IDENTITY_FILE=/offline/aria-age-key.txt
```

The verification command checks the encrypted envelope before requesting the private identity. A
full verification decrypts into a temporary directory, rejects unexpected or non-regular archive
members, verifies every nested digest, and asks `pg_restore` to inspect the custom dump. The restore
drill goes further: it restores into a randomly named `aria_restore_drill_*` database, requires the
literal `RESTORE-DRILL` acknowledgement, confirms restored public tables exist, and removes that
database in a `finally` boundary. It has no option for naming or overwriting the live database.

For a disaster recovery event, first recover the pair of R2 objects into an isolated host, run the
full verification and restore drill, then stop writers and restore into a new PostgreSQL instance.
Validate source, artifact, evidence, graph, comparison, and review counts before changing any
application database endpoint. Restore the included filesystem artifact archive only into a new
empty artifact volume. Never unpack it over a running evidence store.

Schedule the encrypted backup daily from the host and run a restore drill at least weekly. Retain
multiple generations in R2 so operator error or a late-discovered corruption is not immediately
propagated to every recovery point.

## 4D.3 least-privilege operator access

The operator console now has independent permissions for console access, source operations,
workflow recovery, textual-change review, impact review, textual-change publication, impact
publication, and audit-log access. Migrations provision these composable Django groups:

| Group | Granted boundary |
| --- | --- |
| ARIA Viewer | Read the operator console |
| ARIA Source Operator | Read and operate source admission, polling, and retirement |
| ARIA Workflow Operator | Read, retry workflows, and request evidence-bounded summaries |
| ARIA Change Reviewer | Read and append textual-change decisions |
| ARIA Impact Reviewer | Read and append regulatory-impact decisions |
| ARIA Publisher | Read and publish reviewed changes and impacts |
| ARIA Auditor | Read and inspect the audit trail |

Roles are additive. Assign them to active staff users in Django Admin. A superuser keeps every
boundary for emergency administration. `ARIA_ENFORCE_OPERATOR_ROLES=false` is the migration-safe
local default: existing staff continue to work while roles are assigned. Before exposing ARIA,
assign and test at least two administrator accounts, then set it to `true`. Mutation controls are
hidden when a role lacks authority, and every direct denied attempt is fail-closed with an
append-only `console.permission_denied` audit event.

Console sign-in failures are keyed by a SHA-256 of the username and direct peer address, never the
password. The Compose deployment shares these counters through Redis database 2. After
`ARIA_LOGIN_RATE_LIMIT_ATTEMPTS` failures in the configured window, login returns HTTP 429 with a
`Retry-After` header. Successful authentication clears that narrow key. Operator sessions expire
after one hour by default, end when the browser closes, and slide only while actively used.

For production, set `ARIA_REQUIRE_SEPARATE_PUBLISHER=true`. A person who recorded the current
confirmation cannot publish that same textual change, and a person who approved or amended an
impact cannot publish that same impact. Change publications now retain `published_by`, matching the
existing impact publication attribution, and include the publisher identifier in the immutable
event. The rule is also enforced below the console in the publication services, so a management
command cannot bypass it. The change-publication command accepts `--publisher`; the Make target
requires `PUBLISHER=<active-staff-username>`.

## 4D.4 private observability

`/health/metrics/` exposes Prometheus text only when `ARIA_METRICS_TOKEN` is configured and the
request supplies that exact bearer token. When disabled it returns 404; a wrong or missing token
returns 403. Generate a long random value and keep the route private. Do not place this endpoint
behind the public reader hostname.

The metrics deliberately use bounded state labels. Per-source reliability metrics add a stable
source key derived from repository authority and collection slugs plus the endpoint UUID, allowing
operators to distinguish configured endpoints without exposing their display names or network
locations. They report enabled endpoint health, source-run and change-workflow states, outbox
delivery states, review backlogs, the append-only source-structure incident count, and a
scheduler-worker heartbeat. Rolling 24-hour aggregate metrics add source success,
changed-versus-unchanged artifact throughput, p50/p95 source-run and workflow latency, oldest
active-work age, and aggregate artifact, extraction, graph, and embedding coverage. The bounded
per-source metrics add current reliability and HTTP health, freshness headroom, poll delay,
last-success age, consecutive failures, seven-day scheduled-run outcomes, stage coverage, and
stable finding codes. Official URLs, domains, source display names, document titles, artifact
hashes, user IDs, profile names, finding details, and evidence text never become metric labels.

The minute-level heartbeat is published by Beat to the normal discovery queue and written to the
shared cache when a core worker executes it. Its age therefore proves the scheduler, broker, and a
core worker can complete a small task together. `+Inf` is expected when only the lightweight reader
is running; it becomes an alert only when scheduled workers are supposed to be active.

Set `ARIA_METRICS_TOKEN`, `ARIA_GRAFANA_ADMIN_USER`, and a distinct
`ARIA_GRAFANA_ADMIN_PASSWORD` in `.env`, then run:

```text
make observability-check
make start-observability
```

The opt-in Compose profile runs self-hosted Prometheus on `127.0.0.1:9090` and Grafana on
`127.0.0.1:3002` by default. Prometheus keeps its token in a mode-077 temporary file and persists
time series in a named volume. Grafana has anonymous access and sign-up disabled, persists only its
local state, and provisions the read-only `ARIA Pipeline Performance` and `ARIA Source Reliability
& Freshness` dashboards plus the private Prometheus data source directly from the repository. Both
services are excluded from `make start`. The base profile keeps them outside the edge network; the
VPS override permits only Grafana to join it through the stable `aria-grafana` alias. Grafana uses
the provisioned ARIA Pipeline Performance dashboard as its server home page, so an authenticated
operator lands on live ARIA metrics instead of Grafana's generic welcome screen.

Before enabling the production hostname, reach the dashboard through an SSH tunnel:

```text
ssh -L 3002:127.0.0.1:3002 memora_vps
```

Then open `http://localhost:3002` and sign in with the configured Grafana credentials. The
dashboard's six headline numbers show 24-hour source success, lowest evidence coverage, p95 source
and workflow latency, oldest active work, and the human-review backlog. The remaining panels show
throughput, evidence-stage coverage, workflow states, source health, and operational exceptions.
Its dashboard link opens `ARIA Source Reliability & Freshness`, which filters by source and shows
current reliability, freshness headroom, poll delay, last-success age, per-stage coverage,
scheduled-run outcomes, consecutive failures, and current finding codes. The source dashboard
links back to the aggregate scorecard and defaults to a seven-day window.

For the production HTTPS route, provision the repository's Caddy configuration and a Cloudflare
DNS-only `A` record for `metrics.aria.axelyn.com`. The nested hostname is not covered by the
standard `*.axelyn.com` Universal SSL certificate, so proxying it requires an additional edge
certificate that explicitly includes the hostname. Caddy obtains and renews the public certificate
in the DNS-only arrangement. Production pins Grafana's canonical root URL to that hostname, keeps
its host-published port on loopback, and permits the Caddy edge network to reach only the Grafana
container alias. Prometheus never joins the edge network or receives a public route. Grafana
authentication remains mandatory. Cloudflare proxying and Access can be added after an edge
certificate and identity policy have been configured for the nested hostname.

Every HTTP response also carries a bounded `X-Request-ID`. A safe incoming ID is preserved for
cross-service tracing; malformed or log-injection-shaped values are replaced with a UUID. The same
ID is added to in-request logs and permission-denial audit details, while background logs use `-`.

## 4D.5 supply chain and container isolation

All external images used by the Dockerfile and Compose stack are pinned to immutable SHA-256
digests. Python production and browser dependency graphs are resolved to exact versions with hashes
in `requirements.lock` and `requirements-browser.lock`; image builds install them with
`--require-hashes`. The reader continues to use `npm ci`, so its committed npm lock and integrity
values remain authoritative. A version range or a package artifact without an approved hash now
fails the supply-chain gate rather than being silently selected during a later build.

`compose.production.yaml` is an additive production overlay. It runs application processes as the
unprivileged `aria` user, makes each root filesystem and source mount read-only, drops Linux
capabilities, disables privilege escalation, and gives every long-running component CPU, memory,
and process ceilings. Only ingestion, browser, and OCR workers can write the evidence artifact
volume. The reader, migrations, scheduler, and backup process receive it read-only. The API and
Prometheus ports must resolve to `127.0.0.1`; Cloudflare Tunnel or another authenticated local proxy
is the intended ingress boundary.

Run these checks before a deployment:

```text
make supply-chain-check
make security-scan
make sbom
docker compose -f compose.yaml -f compose.production.yaml up -d --wait
```

The first command resolves the final Compose model without expanding application secrets, then
rejects mutable image references, unhashed Python dependencies, missing isolation controls, public
bind addresses, excessive write access, or absent resource ceilings. Its invariant logic also has
unit tests. `security-scan` runs a digest-pinned, self-hosted Trivy filesystem scan and fails on high
or critical dependency, configuration, or committed-secret findings. It excludes the ignored local
`.env`; that file must remain outside version control and should be checked through the deployment
secret-management procedure. `sbom` runs digest-pinned Syft locally and writes an ignored SPDX JSON
inventory to `build/aria-sbom.spdx.json` for each release candidate.

Digest and lock updates are deliberate maintenance: review upstream release notes, regenerate both
Python locks in the same Python 3.13 environment, run all four image builds, rerun the scan and test
suite, and commit the new digests and locks together. The current Debian package and Playwright
installation steps still contact their upstream repositories during a clean build, so this phase
does not claim bit-for-bit offline reproducibility. The immutable base, language-package hashes,
SBOM, scanner, and runtime boundaries materially narrow and expose that remaining surface.

## 4D.6 production readiness and release gate

ARIA now distinguishes code readiness from deployment readiness. `make release-check` is the local
code gate: it resolves production isolation, builds every runtime plus a dependency-hashed test
image, runs Python lint, rejects model changes without migrations, executes the complete backend and
bounded-browser suites, runs frontend lint/tests/coverage/build, validates Prometheus, scans all
three Python locks plus npm and Docker configuration, and emits the SPDX SBOM. A release candidate
is not ready when any one of those stages fails.

`make production-readiness` is the live deployment gate. The production overlay forces debug off,
HTTPS proxy handling, redirect and secure cookies, a short initial HSTS policy, strict operator
roles, two-person publication, R2 evidence storage, and encrypted off-host backup upload. The gate
then requires:

- a strong Django secret and explicit public hostname without a wildcard;
- password-protected internal PostgreSQL, Redis cache, and Redis broker endpoints;
- scoped private R2 credentials, an age public backup recipient, and a private metrics token;
- a commit SHA injected as the release identity;
- no unapplied database migrations;
- a successful shared-cache round trip and broker ping; and
- a successful temporary R2 write, digest read-back, deletion, and deletion confirmation.

The command reports only check identifiers and bounded status messages; it never echoes a secret,
credential-bearing URL, or object content. The R2 probe uses the existing `.aria-probe/` namespace
and removes its temporary object before passing. Run it only after the private hostname, Cloudflare
Tunnel, R2 credentials, metrics token, backup recipient, and operator roles are configured:

```text
make release-check
make production-readiness
```

Passing the release gate proves the candidate is internally consistent. Passing the live gate
proves that the selected environment meets ARIA's deployment policy and its critical dependencies
are reachable at that moment. Neither replaces a restore drill, autonomous source-soak evidence, or
human review of regulatory output.
