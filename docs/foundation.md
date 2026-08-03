# ARIA Core foundation

## Scope

This foundation implements Phase 1 of the ARIA MVP plan and deliberately stops at the connector
boundary. It provides enough durable structure to add RSS and HTML discovery without redesigning
workflow state later.

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
       +---- HTTP and browser queues (reserved)
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
5. Outbox delivery is intentionally not marked complete yet. Phase 2 must choose and test the
   delivery transport before a publisher is enabled.

## Self-hosting and Cloudflare

PostgreSQL and Redis are self-hosted Compose services without host port exposure. The application
is bound to `127.0.0.1` unless `ARIA_BIND_ADDRESS` is changed. A reverse proxy or Cloudflare Tunnel
can be added at the host boundary later. Cloudflare R2 is the intended hosted exception for raw
artifact storage; local development can use any S3-compatible implementation once Phase 2 begins.

## Next slice

1. Add an HTTP client with domain allowlisting, SSRF protection, response limits, retries, and
   conditional request support.
2. Implement RSS/Atom and static HTML listing connectors.
3. Add immutable raw artifacts and observations backed by an S3-compatible adapter.
4. Route fetched artifacts to the reserved extraction queues.
