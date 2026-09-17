# ARIA Core foundation

## Scope

This foundation implements Phase 1, the Phase 2 JPDP retrieval slice, Phase 3A deterministic
extraction and evidence projection, Phase 3B quality and linked-file remediation, Phase 3C
evidence-backed version comparison, Phase 3D.0–3D.5 continuous official-source monitoring,
downstream orchestration and operator review, Phase F self-hosted OCR completion, Phase G hybrid
semantic retrieval, and the Phase 4A/4B authenticated React reader. It provides durable
registry, workflow, retrieval, immutable evidence, document versioning, OCR lineage, graph, search,
comparison, review, monitoring, and extraction-quality boundaries.

## Runtime layout

```text
React reader / operator
       |
       v
 Django auth + reader APIs + console ---- PostgreSQL + pgvector
       |                       |
       |                       +---- pipeline events + outbox + audit
       |                       +---- local + optional OpenRouter vectors
       v
 Redis broker <--------- Celery beat
       |
       +---- discovery worker queue
       +---- HTTP fetch queue ---- immutable artifact storage
       +---- bounded Chromium worker (browser queue)
       +---- extraction queue ---- durable change orchestration
       |                                  |
       |                                  +---- extraction -> quality gate -> graph/vectors
       +---- diff queue ---------- lineage + anchors + deterministic comparisons + GPT summaries
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
- `knowledge`: structural graph nodes/edges, provider-versioned vectors, and retrieval evaluation
- `comparisons`: version lineage, structural anchors, deterministic deltas, review, and summaries
- `quality`: versioned corpus assessments and append-only, provenance-backed findings
- `orchestration`: durable changed-artifact workflows, stage attempts, gates, and recovery
- `console`: staff-only monitoring, review, recovery, summary, publication, and audit surface
- `reader`: active-user search, exact passages, evidence downloads, and reader-only API contract
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
12. Embedding projections are append-only by section, provider, model, dimension, and source-text
    hash. The 384-dimensional local hash remains an offline lexical fallback; the optional
    OpenRouter provider adds semantic retrieval without replacing local vectors.
13. Quality runs hash both their ruleset configuration and the complete immutable collection
    corpus. An unchanged replay reuses the completed run; changed evidence creates a new run.
14. Quality assessment never mutates source artifacts, extracted text, document versions, or
    review state. New changed-artifact workflows require its gate before graph/vector promotion.
15. OCR is a transformation, not a fetch. The official PDF remains the evidence artifact; its
    searchable PDF and text sidecar are separately hashed under `derived/ocr/sha256/` and linked by
    append-only `ArtifactDerivative` records.
16. OCR idempotency uses the source artifact, profile name/version, and complete configuration
    hash. Completed runs are reused, and downstream extraction retains both source and derivative
    hashes in every OCR-derived section locator.
17. Hosted embeddings receive normalized heading/text input only after evidence has been archived
    and verified; hosted vector search also sends its query text. Original files, credentials,
    provenance, graph state, and stored vectors remain in operator-controlled services.
18. Version comparisons never cross representation tracks or compare re-extractions of the same
    artifact. They produce textual candidates, not claims about legal effect.
19. GPT summaries require currently confirmed change items and cannot alter deterministic change
    classifications. Only explicit reviewed-change publication creates an outbox event.
20. Successful offline replay cannot reset an external source's health. Only a persisted endpoint
    observation can do so.
21. A changed HTTP payload is evidence of changed bytes, not a new legal version. Extraction and
    normalized-content identity must independently establish a new version.
22. Detail pages and approved feeds are first-class monitored resources with independent cadence,
    validators, health, immutable response chains, and immutable link-set snapshots.
23. Resource scheduling is staggered, globally bounded, bounded per endpoint, and overlap-safe.
    An official-domain link outside configured scope is registered disabled for review; an unsafe
    or ambiguous link is quarantined and never becomes a fetch candidate.
24. Only `ArtifactObservation.content_changed=true` creates a change orchestration. Each workflow
    has one SHA-256 idempotency key and append-only per-stage attempts; equivalent normalized text
    terminates without quality, graph, lineage, or comparison work.
25. A genuine new version must pass quality and provenance gates before deterministic comparison.
    Material comparison items stop at human review. GPT summaries can be queued only after every
    non-unchanged item has a current decision and at least one is confirmed.
26. Recovery atomically marks pending or stale work as queued, so repeated beat passes cannot
    duplicate dispatch. Failed work requires an explicit retry; OCR-ready work resumes from its
    derivative evidence without extracting the official source again.
27. The operator console delegates every mutation to the same locked domain services used by
    workers and commands. Actions are POST- and CSRF-protected, staff-only, overlap-safe, and
    actor-audited; comparison reviews remain append-only and publication remains explicit.
28. Browser execution is explicit per endpoint and isolated from general workers. Every request is
    allowlisted, public-DNS validated, IP-pinned, read-only, resource-bounded, and represented by
    append-only evidence; original responses and rendered DOM derivatives are both preserved.
29. The React reader is compiled from locked TypeScript, Vite, Tailwind, and repository-owned
    shadcn source. Django serves hashed assets on the same origin, retains the session/CSRF boundary,
    and can switch atomically to the retained server-rendered templates.

## Self-hosting and Cloudflare

PostgreSQL with pgvector and Redis are self-hosted Compose services without host port exposure. The application
is bound to `127.0.0.1` unless `ARIA_BIND_ADDRESS` is changed. A reverse proxy or Cloudflare Tunnel
can be added at the host boundary later. Cloudflare R2 is the intended hosted exception for raw
artifact storage. The default filesystem backend remains fully self-hosted in a named Docker
volume.

OpenRouter-hosted embeddings are an opt-in exception. The repository and `.env.example` keep
`local_hash` as the offline default. A deployment can activate OpenRouter for better semantic
retrieval or select `local_hash` per request without changing or deleting either projection set.

## Next slice

1. Harden the Cloudflare Access and Tunnel private-preview deployment.
2. Collect task-based reader feedback and measured search interaction telemetry without recording
   query content by default.
3. Add an operator-selected delivery adapter for reviewed outbox events.
