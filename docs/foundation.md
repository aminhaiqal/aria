# ARIA Core foundation

## Scope

This foundation implements Phase 1, the Phase 2 JPDP retrieval slice, Phase 3A deterministic
extraction and evidence projection, Phase 3B quality and linked-file remediation, and Phase F
self-hosted OCR completion. It provides durable registry, workflow, retrieval, immutable evidence,
document versioning, OCR lineage, graph, search, and extraction-quality boundaries.

## Runtime layout

```text
Browser / operator
       |
       v
 Django API + Admin ---- PostgreSQL + pgvector
       |                       |
       |                       +---- pipeline events + outbox + audit
       v
 Redis broker <--------- Celery beat
       |
       +---- discovery worker queue
       +---- HTTP fetch queue ---- immutable artifact storage
       +---- browser queue (reserved)
       +---- extraction queue ---- versions + evidence graph + search indexes
       |                                  |
       |                                  +---- offline quality assessment
       +---- dedicated OCR worker ---- immutable searchable PDF + text sidecar
       +---- normalization, diff queues (reserved)
```

Compose runs a general worker for discovery, fetch, extraction, and reserved workloads. OCR has a
separate concurrency-one worker image containing OCRmyPDF, Tesseract, and the `msa` and `eng`
language packs. This bounds resource use and keeps OCR system packages out of the API image.

## Domain boundaries

- `authorities`: official publisher identity and evidence trust classification
- `collections`: logical families of publications owned by an authority
- `sources`: technical endpoints and versioned connector configuration
- `discovery`: source runs, candidates, observations, connector registry, and scheduling
- `fetching`: safe HTTP retrieval, retries, conditional requests, and fetch-attempt history
- `artifacts`: content-addressed bytes, observations, and immutable derivative lineage
- `extraction`: deterministic extractor runs, extracted documents, and traceable blocks
- `ocr`: versioned OCR plans, execution state, toolchain evidence, and derivative orchestration
- `documents`: stable identities, immutable versions, evidence records, and normalized sections
- `knowledge`: structural graph nodes/edges and local vector projections
- `quality`: versioned corpus assessments and append-only, provenance-backed findings
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
9. Extraction reads only the backend recorded on each artifact and verifies size and SHA-256 before
   parsing. It never downloads the live source URL.
10. Document identity uses collection plus canonical URL. Within that identity, equal normalized
    content reuses one immutable version while each extraction retains its evidence link.
    When an official detail page points to a primary file, the page URL remains the identity and
    the actual file URL remains in artifact/version provenance. Earlier file-URL identities are
    retained with deterministic `superseded_by` pointers rather than deleted.
11. Graph edges are deterministic structural projections and always record their source object.
    Phase 3A does not infer legal meaning or cross-document legal relationships.
12. The 384-dimensional local hash projection is deterministic and private, but lexical rather
    than semantic. Its provider boundary can later target a self-hosted embedding model.
13. Quality runs hash both their ruleset configuration and the complete immutable collection
    corpus. An unchanged replay reuses the completed run; changed evidence creates a new run.
14. Quality assessment is diagnostic. It never mutates source artifacts, extracted text,
    document versions, graph projections, or review state.
15. OCR is a transformation, not a fetch. The official PDF remains the evidence artifact; its
    searchable PDF and text sidecar are separately hashed under `derived/ocr/sha256/` and linked by
    append-only `ArtifactDerivative` records.
16. OCR idempotency uses the source artifact, profile name/version, and complete configuration
    hash. Completed runs are reused, and downstream extraction retains both source and derivative
    hashes in every OCR-derived section locator.

## Self-hosting and Cloudflare

PostgreSQL with pgvector and Redis are self-hosted Compose services without host port exposure. The application
is bound to `127.0.0.1` unless `ARIA_BIND_ADDRESS` is changed. A reverse proxy or Cloudflare Tunnel
can be added at the host boundary later. Cloudflare R2 is the intended hosted exception for raw
artifact storage. The default filesystem backend remains fully self-hosted in a named Docker
volume.

## Next slice

1. Define reviewed structural anchors for acts, regulations, circulars, and guidelines.
2. Compare explicit immutable versions without claiming legal meaning from formatting noise.
3. Persist evidence-backed change candidates with section and artifact provenance.
4. Add RSS/Atom discovery and a browser retrieval fallback for explicitly approved sources.
5. Publish reviewed change events through the outbox.
