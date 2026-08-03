# Phase G hybrid semantic embeddings

## Scope

Phase G adds an optional OpenAI embedding provider without weakening ARIA's evidence boundary.
Official artifacts are still archived, hashed, OCRed, and extracted before any hosted request.
PostgreSQL and pgvector remain the system of record for vectors and retrieval.

The local `aria-token-hash-v1` projection remains available for offline operation. It is a lexical
baseline, not a semantic model. OpenAI `text-embedding-3-small` is stored beside it under a separate
provider/model identity.

## Data boundary

When OpenAI is selected for projection, ARIA sends this normalized string for each section:

```text
{section heading}\n{section text}
```

When an API search selects the OpenAI provider, ARIA also sends the search query text to create its
query vector. Full-text searches and searches selecting `local_hash` make no hosted request.

ARIA does not send source PDF bytes, Cloudflare R2 credentials, artifact observations, graph edges,
quality findings, or database credentials. The returned 384-dimensional vector is stored in the
self-hosted PostgreSQL service.

The OpenAI API documentation states that API data is not used to train models by default. Standard
embeddings requests may be retained for abuse monitoring, while approved Zero Data Retention
projects can use the embeddings endpoint without application-state retention. Review the current
[OpenAI data controls](https://developers.openai.com/api/docs/guides/your-data) before processing
non-public material.

## Configuration

Keep the API key only in ignored `.env` or a deployment secret store:

```dotenv
OPENAI_API_KEY=<project-scoped-secret>
ARIA_EMBEDDING_PROVIDER=openai
ARIA_EMBEDDING_DIMENSIONS=384
ARIA_OPENAI_EMBEDDING_MODEL=text-embedding-3-small
ARIA_OPENAI_EMBEDDING_BATCH_SIZE=64
ARIA_OPENAI_TIMEOUT_SECONDS=60
ARIA_OPENAI_MAX_RETRIES=3
```

The OpenAI embeddings API supports reducing the default vector size through its `dimensions`
parameter. ARIA requests 384 dimensions so the existing pgvector field and HNSW cosine index do not
need a migration. See the official [embedding guide](https://developers.openai.com/api/docs/guides/embeddings).

Set `ARIA_EMBEDDING_PROVIDER=local_hash` to return to fully offline query embeddings immediately.
Existing OpenAI projections remain append-only and inspectable; they are not deleted or rewritten.
New document versions always receive the local projection; when OpenAI is active, its hosted
projection is queued in addition. The `embedding_provider` search parameter can select either
provider per request.

## Operations

Populate missing vectors for only the latest immutable version of every active identity:

```bash
docker compose exec api python manage.py embed_sections --provider openai --sync
```

Omit `--sync` to send the collection job to Celery's `normalization` queue. Retryable connection,
timeout, server, and rate-limit errors use bounded exponential retries. Permanent authentication or
request errors fail without retrying forever.

Projection identity is the normalized section, provider, model, and source-text SHA-256. A replay
does not call OpenAI for completed projections. New document versions or changed section hashes
produce new vectors without mutating earlier records.

Compare providers using the versioned eight-query JPDP benchmark:

```bash
docker compose exec api python manage.py evaluate_embeddings
```

The benchmark is vector-only so PostgreSQL full-text ranking cannot hide embedding behavior. It
deduplicates ranked sections into document identities and reports MRR plus hit@1, hit@3, and hit@5.
Normal API searches continue to support reciprocal-rank-fused `hybrid` retrieval.

## Verified JPDP result

On 2026-08-04:

- one bounded credential check returned a 384-dimensional vector;
- 392 current JPDP sections were embedded in seven requests using 136,583 input tokens;
- replay created zero records, made zero API calls, and consumed zero tokens;
- the local baseline scored MRR `0.271`, hit@1 `0.000`, hit@3 `0.625`, hit@5 `0.625`;
- OpenAI scored MRR `0.646`, hit@1 `0.375`, hit@3 `1.000`, hit@5 `1.000`;
- all eight expected documents appeared in the OpenAI top three;
- the API, PostgreSQL, Redis, general worker, and OCR worker remained healthy;
- all 42 automated tests passed with external calls mocked.

These numbers describe this small JPDP benchmark, not universal retrieval quality. Benchmark cases
and expected publication URLs are versioned in `aria.knowledge.evaluation`; future corpus changes
should be evaluated before changing provider or dimensionality.
