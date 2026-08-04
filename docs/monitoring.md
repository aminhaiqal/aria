# Phase 3D official-source monitoring

## Scope

Phase 3D.0 and 3D.1 turn the approved JPDP connector into a continuous, evidence-backed monitor.
They identify changed HTTP bytes and retrieve new official artifacts; they do not declare that a
legal change occurred. Phase 3C retains that separate deterministic comparison and human-review
boundary.

```text
Celery Beat every minute
  -> claim enabled endpoints whose next_poll_at is due
  -> immutable SourceRun
  -> allowlisted conditional endpoint request
  -> immutable EndpointObservation
  -> observed candidates
  -> conditional artifact requests
  -> immutable R2 artifacts and ArtifactObservations
  -> extraction/versioning only when candidate bytes changed
```

JPDP is currently the only enabled official source. Its configured cadence is six hours. The
scheduler's one-minute tick only finds due work; it does not request JPDP every minute.

## Monitoring contract

Each endpoint response creates an append-only `EndpointObservation` linked to exactly one source
run and its previous observation. It records the request validators, response status, selected
public cache/evidence headers, redirect chain, resolved public addresses, byte count, content hash,
connector version, and one outcome:

- `changed`: a successful response has a different SHA-256 hash;
- `unchanged`: the server returned the same bytes with HTTP 2xx;
- `not_modified`: the server returned HTTP 304 and the prior content evidence was retained.

The next run receives a reproducible cursor containing the prior observation, source run, content
hash, `ETag`, and `Last-Modified`. If no validator is available, ARIA downloads the bounded endpoint
response and compares its hash.

A failed network run marks the endpoint `degraded`. Three consecutive failed runs mark it
`unhealthy`; a later successful observed run resets the counter. Offline extraction or routing
replays cannot alter external-source health.

## Retrieval safety and change handling

Every endpoint, detail-page, artifact, and redirect target must use HTTPS, match the endpoint's
domain allowlist, resolve exclusively to public addresses, use port 443, and stay within the byte,
redirect, timeout, retry, and per-domain rate limits configured in `.env`.

Known artifact URLs receive their latest `ETag` and `Last-Modified` validators. If a listing returns
HTTP 304, its previous candidate set is reused so known documents are still conditionally checked
for a same-URL replacement. A PDF URL that returns HTML, or another extension/content mismatch, is
rejected before any bytes are stored.

Each artifact observation records `content_changed`. HTTP 304 and HTTP 200 with the same candidate
hash do not enqueue extraction again and do not regress an already versioned candidate's state.
New bytes are content-addressed and stored under:

```text
sources/<authority-slug>/<collection-slug>/sha256/<prefix>/<sha256>
```

For JPDP in Cloudflare R2 this begins with:

```text
sources/personal-data-protection-commissioner-malaysia/
  act-709-regulatory-publications/sha256/
```

Existing artifacts remain at their original immutable keys; they are never renamed or overwritten.

## Operations

Celery Beat schedules normal runs automatically. Queue one additional bounded JPDP cycle with:

```bash
make poll-jpdp
```

For an operator-attended synchronous discovery check:

```bash
docker compose exec api python manage.py poll_jpdp --sync --json
```

Synchronous means discovery runs in the command process. Candidate artifact retrieval still uses
the isolated `http_fetch` queue. Inspect terminal attempts with the administrator API or Admin:

| Resource | Endpoint |
|---|---|
| Source endpoints | `/api/v1/source-endpoints/` |
| Source runs | `/api/v1/source-runs/` |
| Endpoint observations | `/api/v1/endpoint-observations/` |
| Candidates | `/api/v1/candidates/` |
| Fetch attempts | `/api/v1/fetch-attempts/` |
| Artifact observations | `/api/v1/artifact-observations/` |

All resources are administrator-only. Endpoint observations and artifacts are read-only.

## Interface sequence

Phase 3D.5 will add the self-hosted operator console for source health, runs, quarantines,
comparison review, and GPT-summary approval. Django Admin remains the operational interface until
then. The polished reader-facing search, evidence timeline, and reviewed-change interface belongs
to Phase 4, after real temporal comparisons exist.

## Live JPDP verification

On 2026-08-04, one bounded manual cycle completed against the live approved JPDP endpoint:

- the endpoint returned HTTP 200 without `ETag` or `Last-Modified`, so ARIA retained its 396,506-byte
  SHA-256 baseline for hash comparison;
- 20 candidates were observed;
- 19 official artifacts returned HTTP 304 and were not extracted again;
- one official HTML page returned changed raw bytes and was stored in Cloudflare R2 under the new
  source-specific namespace;
- that HTML artifact extracted successfully, but its normalized content matched an existing
  document version, so ARIA created no new legal-content version and no comparison;
- endpoint health remained `healthy` with zero consecutive failures.

This result demonstrates the intended boundary: changed website bytes alone do not become a
regulatory-change claim. The 26 focused monitoring/retrieval tests and complete 73-test repository
suite pass with external requests mocked.

## Next slices

Phase 3D.2 should add independently conditional detail-page monitoring and official RSS/Atom feed
discovery. This closes the case where a detail page changes while a validator-enabled parent
listing remains `304`. Phase 3D.3 will then automate identity-safe routing from genuinely new
artifacts into extraction, anchor projection, and eligible comparison.
