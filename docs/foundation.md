# ARIA Core foundation

## Scope

This foundation implements Phase 1 of the ARIA MVP plan and the first Phase 2 retrieval slice. It
provides durable registry, workflow, discovery, retrieval, and raw-evidence storage boundaries.

## Runtime layout

```text
Browser / operator
       |
       v
 Django API + Admin ---- PostgreSQL (workflow source of truth)
       |                       |
       |                       +---- pipeline events + outbox + audit
       v
 Redis broker <--------- Celery beat
       |
       +---- discovery worker queue
       +---- HTTP fetch queue ---- immutable artifact storage
       +---- browser queue (reserved)
       +---- extraction, OCR, normalization, diff queues (reserved)
```

Compose runs one worker consuming every queue for an inexpensive development footprint. The queue
names are already stable, so a deployment can split them into isolated worker services without
changing task code.

## Domain boundaries

- `authorities`: official publisher identity and evidence trust classification
- `collections`: logical families of publications owned by an authority
- `sources`: technical endpoints and versioned connector configuration
- `discovery`: source runs, candidates, observations, connector registry, and scheduling
- `fetching`: safe HTTP retrieval, retries, conditional requests, and fetch-attempt history
- `artifacts`: content-addressed raw bytes and append-only provenance observations
- `events`: transactional pipeline history, delivery outbox, and append-only audit history
- `api`: administrator-only read API
- `health`: unauthenticated liveness and dependency readiness probes

## Reliability decisions

1. Source runs use idempotency keys. A scheduled endpoint cannot create the same run twice.
2. Candidates are unique by endpoint and connector-provided fingerprint. Seeing the same candidate
   in a later run creates a new observation, not a duplicate candidate.
3. A pipeline event and its outbox entry are committed in the same database transaction as the
   state transition that produced them.
4. Celery acknowledgement happens after work. A lost worker can redeliver a task; completed or
   permanently failed runs are ignored safely.
5. Outbox delivery is intentionally not marked complete yet. A later phase must choose and test
   the delivery transport before a publisher is enabled.
6. Every request and redirect is validated against the endpoint allowlist, resolved to public IP
   addresses, and connected through one of those validated addresses.
7. Artifact identity is the SHA-256 of the retrieved bytes. Repeat observations reuse the same
   immutable artifact record and storage key.
8. A completed `SourceRun` means discovery is complete and fetch tasks are durably queued. Fetch
   progress and terminal outcomes are tracked separately in `FetchAttempt` records.

## Self-hosting and Cloudflare

PostgreSQL and Redis are self-hosted Compose services without host port exposure. The application
is bound to `127.0.0.1` unless `ARIA_BIND_ADDRESS` is changed. A reverse proxy or Cloudflare Tunnel
can be added at the host boundary later. Cloudflare R2 is the intended hosted exception for raw
artifact storage. The default filesystem backend remains fully self-hosted in a named Docker
volume.

## Next slice

1. Extract text and metadata from archived HTML and PDF artifacts, with OCR fallback.
2. Resolve stable document identity and build immutable document versions.
3. Add RSS/Atom discovery and a browser retrieval fallback for explicitly approved sources.
4. Diff versions and publish evidence-backed change events through the outbox.
