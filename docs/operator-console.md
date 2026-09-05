# Phase 3D.5 self-hosted operator console

## Scope

Phase 3D.5 provides a first-party operational interface at `/console/` without adding a hosted UI
service or JavaScript build chain. It is server-rendered by the existing Django container and reads
the same PostgreSQL evidence records used by workers, commands, Admin, and the read-only API.

The console includes:

- an operational overview of source health, active source/workflow processing, review demand, and
  failed outbox deliveries;
- searchable official endpoints and monitored resources with immutable observation/run history;
- workflow traces linking the official URL, artifact hash, normalized version, quality gate,
  lineage, comparison, and append-only stage attempts;
- side-by-side deterministic before/after anchors with their artifact hashes and source locators;
- append-only human decisions with supersession history;
- bounded GPT summaries of currently confirmed items only;
- explicit, idempotent reviewed-change publication;
- searchable append-only audit history; and
- a governed source-admission workbench with source-pack evidence, deterministic gates, bounded
  capture/pilot controls, candidate and network review, and read-only repair previews; and
- a source-confidence view spanning lifecycle state and end-to-end evidence coverage for every
  official endpoint; and
- an impact-review desk joining exact change evidence, controlled business targets, and append-only
  approval, amendment, rejection, or needs-context history.

This remains an operator surface, separate from the Phase 4A reader at `/reader/`.

## Access and Cloudflare boundary

Create a Django superuser or staff user, then open `http://127.0.0.1:8000/console/`:

```bash
docker compose exec api python manage.py createsuperuser
```

Every console page except the login form and allowlisted CSS/JavaScript assets requires an active
`is_staff` session. Mutation routes additionally require POST and Django CSRF validation. Session
and CSRF cookies are HTTP-only, SameSite Lax, and secure by default whenever `ARIA_DEBUG=false`.

For a Cloudflare Tunnel terminating HTTPS, keep the Compose port bound to loopback and configure:

```dotenv
ARIA_DEBUG=false
ARIA_ALLOWED_HOSTS=aria.example.com,localhost,127.0.0.1,api
ARIA_CSRF_TRUSTED_ORIGINS=https://aria.example.com
ARIA_TRUST_X_FORWARDED_PROTO=true
ARIA_SSL_REDIRECT=true
ARIA_SECURE_COOKIES=true
ARIA_SECURE_HSTS_SECONDS=31536000
ARIA_SECURE_HSTS_INCLUDE_SUBDOMAINS=false
ARIA_SECURE_HSTS_PRELOAD=false
```

Point the tunnel origin to `http://127.0.0.1:${ARIA_WEB_PORT}`. Trust forwarded protocol headers
only when the application is reachable exclusively through the controlled proxy. Health endpoints
remain exempt from Django's HTTPS redirect for internal Compose probes. Enable subdomain HSTS or
preload only after reviewing every hostname in the domain. Cloudflare Access may be added as a
second perimeter, but Django staff authentication remains authoritative.

## Mutation contract

The console is deliberately thin. GET views never run source checks, retry work, write decisions,
queue summaries, or publish events. POST actions call transactionally locked domain operations:

| Action | Guard | Durable result |
|---|---|---|
| Poll endpoint | enabled and no active endpoint run | manual `SourceRun`, audit event, queued task |
| Poll resource | endpoint/resource enabled, explicitly approved, no active run | manual `ResourceRun`, audit event, queued task |
| Retire resource | no active resource run, written reason, exact `RETIRE` confirmation | disabled schedule plus retained evidence, actor-stamped audit and pipeline events |
| Retry workflow | failed, summary-failed, or OCR-ready | retry count/status update, audit event, queued task |
| Record decision | staff, non-unchanged item, valid decision/rationale | new append-only `ComparisonReview` linked to its predecessor |
| Record impact decision | current confirmed source change, controlled targets, valid rationale | new append-only `ImpactReview` and exact target snapshots |
| Generate summary | complete current review, at least one confirmed item, no active summary | audit event and bounded GPT task |
| Publish changes | complete review, confirmed item, exact `PUBLISH` confirmation | idempotent publication + pipeline/outbox event + audit event |
| Capture admission pilot | disabled JavaScript endpoint, installed source pack, no active run | bounded `SourceRun`, browser evidence, audit event |
| Assess admission | two to five completed captures | immutable, deduplicated gate assessment and audit event |
| Promote admission | current ready assessment, exact `PROMOTE` confirmation | immutable promotion, enabled schedule, pipeline and audit events |
| Run static pilot | disabled static endpoint, installed source pack, no active run | ordinary bounded `SourceRun`, exact pack/config evidence, audit event; no schedule |
| Assess static source | two to five completed runs and downstream evidence | immutable run IDs, candidate hash, deterministic gates and audit event |
| Promote static source | exact current ready assessment and `PROMOTE` | locked re-evaluation, immutable promotion, enabled bounded schedule |

Failed or duplicate actions return an operator message without bypassing their invariant. A retry
does not delete prior attempts. A changed decision appends a new review. Repeated publication skips
an already published confirmation review rather than duplicating its event.

The impact desk is available at `/console/impacts/`. It never treats a candidate as approved merely
because it was generated by GPT or deterministic rules. Reviewers see the exact source anchors,
artifact hashes, candidate wording, and versioned applicability terms before deciding. A later
source-review decision invalidates the old candidate's review eligibility; ARIA preserves its
history and requires a new candidate from current evidence.

Resource retirement is similarly idempotent. Repeating it does not replace the first actor or
reason and does not duplicate events. Source-pack synchronization may refresh non-decision metadata
but cannot re-enable a retired URL; restoring one therefore requires a future explicit governed
workflow rather than a side effect of discovery.

GPT output remains presentation data: the prompt receives only bounded before/after text for
currently confirmed items, must preserve deterministic IDs and change types, and must assert that
legal effect was not assessed. It cannot publish or modify evidence.

The admissions area is available at `/console/admissions/`. Promotion re-runs the exact assessment
under a database row lock. A changed capture set, candidate set, connector contract, or source-pack
checksum makes the submitted assessment stale and blocks activation.

Deterministic static-source admission appears on the ordinary source detail page. The read-only
`/console/confidence/` page consolidates source-pack, lifecycle, admission, scheduling, reliability,
artifact, extraction, graph, and embedding evidence. Its percentage measures record coverage only;
it is not legal or predictive confidence.

## Operations

The console remains part of the existing API image, so it needs no new service. Phases 3D.9–3D.10
add source-pack and admission-evidence migrations, which the Compose migration service applies
before the API and workers start:

```bash
docker compose up --build -d
docker compose ps
docker compose exec -T api python manage.py check --deploy
```

The root route now redirects to the authenticated `/reader/` preview. The operator surface remains
at `/console/`; existing Admin, health, and administrator API routes remain unchanged.
Static console assets are vendored and served through an exact two-file allowlist, so the login page
does not depend on a CDN or internet access.

Pending outbox rows are durable events awaiting a future delivery adapter, not delivery failures,
so their normal accumulation does not trigger operator attention. The dashboard alerts on failed
delivery attempts instead. Its active-processing count includes pending/running source runs as well
as changed-artifact orchestration, preventing queued collection work from appearing idle.

## Verification

The Phase 3D.5 focused suite covers staff-only access, static-asset allowlisting, CSRF, POST-only
mutations, endpoint/resource approval and overlap guards, read-only workflow rendering, safe retry,
exact evidence rendering, append-only review supersession, rationale validation, summary eligibility
and deduplication, exact publication confirmation, event idempotency, and actor audit records.

Run it independently with:

```bash
docker compose exec -T api python manage.py test tests.test_console
```
