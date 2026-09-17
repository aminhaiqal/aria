# Phase G hybrid semantic embeddings

## Scope

Phase G provides optional hosted semantic embeddings without weakening ARIA's evidence boundary.
Official artifacts are archived, hashed, OCRed, and extracted before any hosted request.
PostgreSQL and pgvector remain the system of record for vectors and retrieval.

The local `aria-token-hash-v1` projection remains available for offline operation. It is a lexical
baseline, not a semantic model. Hosted models are called through OpenRouter's OpenAI-compatible
HTTP API and stored under the `openrouter` provider identity. ARIA does not depend on the OpenAI
Python SDK.

## Data and privacy boundary

When OpenRouter is selected for projection, ARIA sends this normalized string for each section:

```text
{section heading}\n{section text}
```

When a search selects the OpenRouter provider, ARIA also sends the query text to create its query
vector. Full-text searches and searches selecting `local_hash` make no hosted request.

ARIA does not send source PDF bytes, Cloudflare R2 credentials, artifact observations, graph edges,
quality findings, or database credentials. The returned 384-dimensional vector is stored in the
self-hosted PostgreSQL service. Requests set OpenRouter provider preferences to require supported
parameters, require Zero Data Retention routing, and deny data-collecting providers by default.
Review the current [OpenRouter ZDR documentation](https://openrouter.ai/docs/guides/features/zdr)
before processing non-public material.

## Configuration

Keep the API key only in ignored `.env` or a deployment secret store:

```dotenv
OPENROUTER_API_KEY=<project-scoped-secret>
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
OPENROUTER_HTTP_REFERER=https://aria.axelyn.com
OPENROUTER_APP_TITLE=ARIA
OPENROUTER_ZDR=true
OPENROUTER_DATA_COLLECTION=deny
OPENROUTER_ALLOW_PROVIDER_FALLBACKS=true
ARIA_EMBEDDING_PROVIDER=openrouter
ARIA_READER_EMBEDDING_PROVIDER=openrouter
ARIA_EMBEDDING_DIMENSIONS=384
ARIA_OPENROUTER_EMBEDDING_MODEL=openai/text-embedding-3-small
ARIA_OPENROUTER_EMBEDDING_BATCH_SIZE=64
ARIA_OPENROUTER_TIMEOUT_SECONDS=60
ARIA_OPENROUTER_MAX_RETRIES=3
```

ARIA requests 384 dimensions so the existing pgvector field and HNSW cosine index need no
migration. OpenRouter's embedding contract is documented in its
[embeddings API reference](https://openrouter.ai/docs/api/api-reference/embeddings/create-embeddings).

Set `ARIA_EMBEDDING_PROVIDER=local_hash` and `ARIA_READER_EMBEDDING_PROVIDER=local_hash` to return
to fully offline embeddings immediately. Historical projections stored under the legacy `openai`
provider identity remain append-only audit records; the cutover does not relabel or delete them.
New hosted projections use `openrouter`.

## Operations

Verify both hosted capabilities without printing content or credentials:

```bash
make verify-openrouter
```

Populate missing vectors for the default JPDP collection:

```bash
docker compose exec api python manage.py embed_sections --provider openrouter --sync
```

Populate every latest immutable section across all collections during a deliberate migration:

```bash
docker compose exec api python manage.py embed_sections --provider openrouter --sync --all
```

Without `--all`, omit `--sync` to send the selected collection job to Celery's `normalization`
queue. Retryable connection, timeout, server, and rate-limit errors use bounded exponential retries.
Permanent authentication or request errors fail without retrying forever.

Projection identity is the normalized section, provider, model, and source-text SHA-256. A replay
does not call OpenRouter for completed projections. New document versions or changed section hashes
produce new vectors without mutating earlier records.

Compare providers using the versioned eight-query JPDP benchmark:

```bash
docker compose exec api python manage.py evaluate_embeddings
```

The benchmark is vector-only so PostgreSQL full-text ranking cannot hide embedding behavior. It
deduplicates ranked sections into document identities and reports MRR plus hit@1, hit@3, and hit@5.
Normal API searches continue to support reciprocal-rank-fused `hybrid` retrieval.

## Historical baseline

The 2026-08-04 benchmark used the previous direct provider integration. It produced 392 current
JPDP section vectors and scored MRR `0.646`, hit@1 `0.375`, hit@3 `1.000`, and hit@5 `1.000` on the
small eight-query set. Those measurements are retained as history, not presented as OpenRouter
cutover results. Run the same versioned evaluation after backfilling OpenRouter vectors to establish
the new comparable baseline.
