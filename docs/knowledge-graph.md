# Phase 3A extraction and evidence graph

## Scope

Phase 3A turns immutable archived JPDP artifacts into queryable, evidence-backed records. The
evidence path is self-hosted. Cloudflare R2 may hold raw bytes; PostgreSQL with pgvector holds
workflow state, normalized text, graph records, full-text indexes, and vector projections. Phase G
adds an optional hosted embedding computation without moving vector storage or source evidence.

```text
RawArtifact (filesystem or R2, immutable)
    |
    v  verified read: byte size + SHA-256
ExtractionRun -> ExtractedDocument -> ExtractedBlock
    |
    v
DocumentIdentity -> DocumentVersion -> NormalizedSection
    |                    |                    |
    +------ evidence ----+                    +-- full-text + pgvector
                         |
                         v
 Authority -> Collection -> Document -> Version -> Section -> RawArtifact
```

## Extraction rules

- Extractors read the stored artifact backend recorded in the database. They never fetch a URL.
- HTML extraction removes layout/script elements, prefers known content containers, and stores a
  DOM path for every block.
- BetterDocs extraction prefers `.betterdocs-entry-content`, records primary official file links
  and their DOM paths, and excludes category/sidebar shells.
- PDF extraction uses pypdf layout mode and deterministically falls back to plain text extraction
  when OCR text lacks layout coordinates. It stores one traceable block per page.
- A PDF averaging fewer than the configured non-whitespace characters per page is marked
  `ocr_required`. Phase 3A routes it to review; it does not pretend that OCR has occurred.
- Extracted output is keyed by artifact, extractor version, and configuration hash. Replaying the
  same input and configuration is idempotent.

Replay all archived artifacts without network access:

```bash
docker compose exec api python manage.py extract_artifacts --sync
```

Omit `--sync` to queue work for Celery. Use `--artifact <uuid>` to select one artifact or `--limit`
for a bounded batch.

## Identity, versions, and provenance

A document identity is derived from its publication collection and canonical URL. Equal normalized
content for the same identity reuses an immutable `DocumentVersion`. Extraction is shared when
different observations contain the same artifact bytes, while every observation still gets a
`VersionEvidence` record linking its identity/version to the raw artifact and extractor run.
Normalized sections retain artifact SHA-256, observed URL, page or DOM locator, and character
offsets. OCR-derived sections use the official PDF as `source_artifact` while their extraction run
points to the searchable derivative; locators also contain the derivative hash, OCR run, profile,
and configuration hash.

For linked publications, the detail-page URL remains the canonical document identity. The fetched
file URL is retained as the artifact observation and version-evidence URL. A deterministic
`superseded_by` pointer excludes earlier file-URL aliases from operational search and quality while
preserving their historical versions and graph evidence.

Phase 3A graph predicates are intentionally limited to:

- `has_collection`
- `has_document`
- `has_version`
- `has_section`
- `derived_from`

These are deterministic structural statements. Legal citations, amendments, obligations, and
other interpreted relationships require a later reviewed extraction phase.

## Retrieval

PostgreSQL provides a GIN full-text index over headings and section text. pgvector provides an HNSW
cosine index over 384-dimensional section projections. The default `aria-token-hash-v1` provider is
a deterministic local lexical projection: it validates the private vector pipeline without calling
a hosted model and must not be described as a semantic embedding model. The optional OpenRouter
provider requests 384-dimensional hosted vectors into the same local schema. Both projection sets
coexist and are independently idempotent.

The administrator-only search endpoint supports `hybrid`, `full_text`, and `vector` modes:

```text
GET /api/v1/knowledge-search/?q=personal+data+protection&mode=hybrid
GET /api/v1/knowledge-search/?q=privacy+risk&mode=vector&embedding_provider=openrouter
```

Hybrid ranking uses reciprocal-rank fusion. Results include the authority, collection, identity,
version content hash, source URL, raw artifact SHA-256, page or DOM locator, and section text.

Other read-only endpoints are:

| Resource | Endpoint |
| --- | --- |
| Extraction runs | `/api/v1/extraction-runs/` |
| Extracted documents | `/api/v1/extracted-documents/` |
| OCR runs | `/api/v1/ocr-runs/` |
| Artifact derivatives | `/api/v1/artifact-derivatives/` |
| Document identities | `/api/v1/documents/` |
| Document versions | `/api/v1/document-versions/` |
| Normalized sections | `/api/v1/sections/` |
| Graph nodes | `/api/v1/graph-nodes/` |
| Graph neighborhood | `/api/v1/graph-nodes/{id}/neighbors/` |
| Graph edges | `/api/v1/graph-edges/` |

Extraction quality is assessed over immutable document versions after projection. See
[Phase 3B extraction quality](quality.md) for its report command and read-only endpoints. See
[Phase F self-hosted OCR](ocr.md) for derivative lineage and operations.

## Configuration

```dotenv
ARIA_EMBEDDING_PROVIDER=local_hash
ARIA_LOCAL_EMBEDDING_MODEL=aria-token-hash-v1
ARIA_EMBEDDING_DIMENSIONS=384
OPENROUTER_API_KEY=
ARIA_OPENROUTER_EMBEDDING_MODEL=openai/text-embedding-3-small
ARIA_PDF_OCR_MIN_CHARACTERS_PER_PAGE=40
```

The vector dimension is schema-bound in Phase 3A and must remain 384. Changing a provider or model
creates a new append-only embedding projection rather than overwriting an earlier one.

Populate and evaluate the optional provider with:

```bash
docker compose exec api python manage.py embed_sections --provider openrouter --sync
docker compose exec api python manage.py evaluate_embeddings
```

See [Phase G hybrid embeddings](embeddings.md) for retry behavior, privacy boundaries, benchmark
cases, and the verified JPDP result.

See [Phase 3C version comparison](version-comparison.md) for representation-safe lineage,
structural anchors, deterministic textual deltas, human review, and optional GPT summaries.

When the S3-compatible backend is selected, validate the prepared R2 configuration with:

```bash
docker compose exec api python manage.py verify_object_storage
```

The command writes one uniquely named temporary probe, reads it back, verifies its hash, deletes
that exact key, and confirms cleanup. It never prints credentials.

## JPDP pilot verification

On 2026-08-03, Phase 3A replayed the 20 archived JPDP artifacts entirely offline. All 20 extraction
runs succeeded and all 8,428,140 raw bytes matched their recorded SHA-256 digests. The result is 20
document identities, 20 immutable versions, 40 version-evidence links covering every archived
observation, 234 traceable sections, 234 local vector projections, 296 graph nodes, and 549
evidence edges. The 191-page PDF yielded text on all 191 pages and did not require OCR. A second
replay left extraction, version, section, node, and embedding counts unchanged.

On 2026-08-04, Phase G added 392 OpenAI projections for the latest versions of the 20 active JPDP
identities. Replay skipped all 392 without an API call. On eight versioned queries, vector-only
retrieval improved from local MRR `0.271` to OpenAI MRR `0.646`; hit@3 improved from `0.625` to
`1.000`, and hit@5 improved from `0.625` to `1.000`.
