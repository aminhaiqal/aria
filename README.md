# ARIA Core

ARIA is an Autonomous Regulatory Intelligence Agent. This repository contains the evidence-first
foundation for discovering, retrieving, and preserving official regulatory publications before
any AI interpretation is introduced.

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
- Explicit pipeline states from discovery through publication and failure handling
- Transactional pipeline events and an outbox ready for a future delivery transport
- Append-only audit records
- Django Admin operations and read-only DRF registry, fetch, provenance, and artifact APIs
- Celery worker queues separated by workload type
- Liveness and database/Redis readiness probes
- A self-hosted Docker Compose stack using PostgreSQL and Redis

RSS discovery, browser retrieval, extraction, OCR, normalization, document identity, and
versioning remain for the next phases.

## Current milestone

- Phase 1 registry and pipeline foundation: complete
- Phase 2 JPDP retrieval pilot: complete
- Phase 2 source breadth: RSS and browser fallback remain open
- Next implementation slice: deterministic HTML/PDF extraction from archived artifacts

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
future extraction and are never mutated.

See [Foundation architecture](docs/foundation.md) and
[Phase 2 retrieval](docs/retrieval.md) for design decisions and the next implementation slice.
