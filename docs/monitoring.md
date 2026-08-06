# Phase 3D official-source monitoring

## Scope

Phase 3D.0–3D.2 turn the approved JPDP connector into a continuous, evidence-backed monitor. They
identify changed HTTP bytes, independently check known detail pages and the approved site feed,
and retrieve new official artifacts. They do not declare that a legal change occurred. Phase 3C
retains that separate deterministic comparison and human-review boundary.

```text
Celery Beat every minute
  -> claim a bounded set of due endpoints and monitored resources
  -> conditional root listing -> immutable EndpointObservation
  -> conditional detail/feed -> immutable ResourceObservation + link snapshot
  -> safe accepted document links -> observed candidates
  -> approved in-scope feed entries -> staggered detail resources
  -> quarantined or out-of-scope links -> evidence/review only
  -> known/new candidates -> conditional artifact requests
  -> immutable R2 artifacts and ArtifactObservations
  -> extraction/versioning only when candidate bytes changed
```

JPDP is currently the only enabled official source. Its root and detail cadence is six hours; the
official English RSS cadence is one hour. The scheduler's one-minute tick only finds due work; it
does not request every resource every minute.

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

## Resource and link contract

`MonitoredResource` gives every approved detail page or feed its own URL, resource type, approval
basis, cadence, validators, health, and capacity state. A `ResourceRun` is linked to a source run so
accepted documents reuse the existing candidate/provenance contract without letting a detail-page
failure degrade the parent listing's health.

Each successful resource check appends a `ResourceObservation`. It records the same bounded HTTP
evidence as the endpoint observation plus a hash of the current link set. Its append-only
`ResourceLinkObservation` rows classify each link as `added`, `retained`, or `removed`, and as
`accepted` or `quarantined`. A 304 copies the prior current link set as retained evidence, which is
important because a linked PDF can change at a stable URL even when its detail page does not.

The feed parser accepts bounded RSS 2.0 and Atom XML and rejects XML entity declarations. A feed
must be explicitly approved before execution. Links leaving the endpoint domain allowlist and
duplicate feed identifiers that point to different URLs are quarantined. Official-domain feed
entries outside `detail_resource_path_prefixes` are registered disabled for operator review.
Removal is evidence, never deletion.

The default scheduler limits resource dispatch to five per minute, three per endpoint per batch,
and 50 enabled resources per endpoint. New resources receive deterministic offsets across their
polling interval, and a resource with a pending or running check cannot overlap itself. These can
be changed with `ARIA_MONITOR_RESOURCE_BATCH_SIZE`,
`ARIA_MONITOR_RESOURCE_BATCH_PER_ENDPOINT`, and
`ARIA_MONITOR_MAX_ENABLED_RESOURCES_PER_ENDPOINT`.

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

Run one explicitly selected approved detail page or feed:

```bash
make poll-resource RESOURCE_ID=<uuid>
docker compose exec api python manage.py poll_resource <uuid> --sync --json
```

`--replay` labels the fresh conditional HTTP observation as operator-requested replay; it does not
reuse or rewrite historical response bytes.

| Resource | Endpoint |
|---|---|
| Source endpoints | `/api/v1/source-endpoints/` |
| Source runs | `/api/v1/source-runs/` |
| Endpoint observations | `/api/v1/endpoint-observations/` |
| Monitored resources | `/api/v1/monitored-resources/` |
| Resource runs | `/api/v1/resource-runs/` |
| Resource/link observations | `/api/v1/resource-observations/` |
| Candidates | `/api/v1/candidates/` |
| Fetch attempts | `/api/v1/fetch-attempts/` |
| Artifact observations | `/api/v1/artifact-observations/` |

All APIs are administrator-only and read-only.

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
regulatory-change claim.

On 2026-08-05, the Phase 3D.2 read-only audit confirmed:

- 18 distinct JPDP detail pages already backed by stored candidate provenance;
- the approved Act 709 page declares `https://www.pdp.gov.my/ppdpv1/en/feed/` as its English RSS
  2.0 site feed;
- separate comments feeds are declared but are intentionally not approved;
- the feed advertises hourly updates and returned bounded official entry metadata.

The pre-deployment gate passed all 85 repository tests with external requests mocked.

The controlled post-deployment pilot then completed without failed resource runs:

- seeding registered 18 approved detail resources and one approved RSS resource;
- the first RSS check returned HTTP 200 with 10,080 bytes and 10 official entry links;
- all 10 entries were outside the configured Act 709 detail path, so they were registered disabled
  for review and none became fetch candidates;
- the second RSS check sent its stored validators, received HTTP 304, and appended 10 retained link
  observations while preserving the prior content and link-set hashes;
- the selected detail page returned HTTP 200 with 395,166 bytes and one accepted official PDF;
- its second check returned the same bytes, recorded `unchanged`, and retained the PDF link;
- both resulting PDF checks returned HTTP 304 and did not enqueue extraction;
- the parent endpoint remained `healthy` with zero failures, while feed and selected detail health
  became independently `healthy`.

## Next slices

Phase 3D.3 now automates identity-safe orchestration from genuinely changed artifacts through
extraction, quality, graph/vector projection, lineage, anchors, and eligible comparison. Equal
normalized content stops early; material deltas wait for human review; GPT summaries are
post-review only. See [Phase 3D.3 change orchestration](orchestration.md).

Phase 3D.5 next adds the self-hosted operator console. It must preserve the same deterministic
comparison, review, and explicit-publication boundaries.
