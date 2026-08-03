# ARIA Core

ARIA is an Autonomous Regulatory Intelligence Agent. This repository contains the evidence-first
foundation for discovering, retrieving, preserving, extracting, and indexing official regulatory
publications before any AI interpretation is introduced.

## What is implemented

- Registry hierarchy: authorities, publication collections, source endpoints, and versioned
  connector configuration
- Scheduled and manual source runs with durable PostgreSQL state
- Idempotent discovered candidates and per-run observations
- A configurable static HTML listing connector, initially seeded for Malaysia's JPDP
- An HTTPS client with domain allowlists, public-address validation, redirect revalidation,
  response limits, retries, conditional requests, and per-domain rate limiting
- Immutable, content-addressed raw artifacts with SHA-256 integrity checks
- Append-only provenance observations and durable fetch-attempt history
- Self-hosted filesystem artifact storage plus an optional S3-compatible Cloudflare R2 adapter
- Deterministic offline HTML and PDF extraction from archived bytes, with page and DOM locators
- BetterDocs detail-page routing to archived primary publications with stable landing-page identity
- Stable document identities, immutable content versions, and append-only evidence links
- A structural evidence graph from authority to raw artifact in PostgreSQL
- PostgreSQL full-text search plus pgvector retrieval with local and OpenAI embedding providers
- Provenance-gated bilingual structural anchors and deterministic immutable-version comparisons
- Append-only human review, optional structured GPT summaries, and reviewed-change outbox events
- Deterministic, corpus-aware extraction quality runs with provenance-backed findings
- Immutable OCR derivatives with source/output hashes, toolchain records, and page metrics
- A dedicated self-hosted OCRmyPDF/Tesseract worker with Malay and English language data
- Explicit pipeline states from discovery through publication and failure handling
- Transactional pipeline events and an outbox ready for a future delivery transport
- Append-only audit records
- Django Admin operations and read-only DRF registry, evidence, graph, and search APIs
- Celery worker queues separated by workload type
- Liveness and database/Redis readiness probes
- A self-hosted Docker Compose stack using pgvector-enabled PostgreSQL and Redis

RSS discovery, browser retrieval, legal-relationship extraction, version diffing, and change
publication remain for later phases. OCR output is an explicit derivative and never replaces or
mutates the official source artifact.

## Current milestone

- Phase 1 registry and pipeline foundation: complete
- Phase 2 JPDP retrieval pilot: complete
- Phase 3A deterministic extraction and evidence graph: implemented
- Phase 3B extraction quality and JPDP linked-file remediation: implemented
- Phase 3C evidence-backed structural version comparison: implemented
- Phase 3F bounded self-hosted OCR completion: implemented
- Phase 3G measured hybrid semantic retrieval: implemented
- Phase 2 source breadth: RSS and browser fallback remain open
- Next implementation slice: RSS/Atom source breadth and reviewed-event delivery adapters

## Local setup

Requirements: Docker Engine with Docker Compose.

```bash
cp .env.example .env
docker compose up --build -d
docker compose exec api python manage.py createsuperuser
```

Open:

- Admin: <http://127.0.0.1:8000/admin/>
- Liveness: <http://127.0.0.1:8000/health/live/>
- Readiness: <http://127.0.0.1:8000/health/ready/>
- Read-only API: <http://127.0.0.1:8000/api/v1/>

The API requires an administrator session or Basic authentication. PostgreSQL and Redis are only
reachable on the private Compose network. Change all `change-me` values before using this outside
local development.

## Common commands

```bash
make bootstrap       # create .env if absent, build, and start
make logs            # follow API and worker logs
make test            # run the Django test suite in Compose
make makemigrations  # generate model migrations
make migrate         # apply migrations
make extract         # replay every archived artifact through extraction locally
make route-linked    # route and queue official files linked by archived JPDP pages
make plan-ocr        # list the deterministic OCR input set and current statuses
make ocr             # process OCR synchronously inside the isolated OCR container
make embed-openai    # populate missing OpenAI vectors for the current JPDP corpus
make evaluate-embeddings # compare local and OpenAI vector retrieval on the JPDP benchmark
make quality         # assess the current JPDP extraction corpus without network access
make audit-lineage   # read-only audit of version provenance and representation lineage
make classify-lineage # persist append-only lineage assessments
make anchors         # project current bilingual legal anchors
make compare         # compare every eligible temporal version pair deterministically
make summarize COMPARISON_ID=<uuid> # summarize confirmed deltas with structured GPT output
make publish-reviewed COMPARISON_ID=<uuid> # create idempotent reviewed-change outbox events
make verify-storage  # write/read/delete one temporary R2 probe
make superuser       # create an admin user
make down             # stop services without deleting data
```

Seed and run the first official source:

```bash
docker compose exec api python manage.py seed_jpdp --run
```

The command registers the Personal Data Protection Commissioner, Malaysia (JPDP), its Act 709
publication collection, and a versioned connector configuration before queueing one manual run.
Discovery completion means the candidates have been persisted and their fetch tasks have been
queued; inspect fetch attempts or worker logs until every candidate reaches a terminal state.

## Cloudflare boundary

No cloud dependency is required: raw bytes use the `artifact-data` Docker volume by default. To
use the prepared Cloudflare account, set `ARIA_OBJECT_STORAGE_BACKEND=s3` and supply the R2 S3 API
endpoint, bucket, access key, and secret in `.env`. Raw artifact bytes are archived before any
extraction and are never mutated. Normalized records, graph edges, full-text indexes, and vectors
remain in the self-hosted PostgreSQL service.

OpenAI embeddings are optional. When selected, ARIA sends normalized section headings and text,
plus the text of OpenAI-backed search queries, to the embeddings endpoint. Source PDFs, R2
credentials, provenance records, and graph data remain local. The deterministic `local_hash`
provider remains available as an offline fallback.

See [Foundation architecture](docs/foundation.md), [Phase 2 retrieval](docs/retrieval.md),
[Phase 3A extraction and evidence graph](docs/knowledge-graph.md),
[Phase 3B extraction quality](docs/quality.md),
[Phase 3C version comparison](docs/version-comparison.md), and
[Phase F self-hosted OCR](docs/ocr.md) for design decisions and operating instructions.
[Phase G hybrid embeddings](docs/embeddings.md) documents the OpenAI embedding boundary and
measured retrieval results.
